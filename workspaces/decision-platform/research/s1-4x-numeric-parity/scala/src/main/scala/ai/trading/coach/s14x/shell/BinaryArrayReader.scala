package ai.trading.coach.s14x.shell

import com.fasterxml.jackson.databind.JsonNode
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import scala.jdk.CollectionConverters.*
import scala.util.control.NonFatal

final case class DecodedBinaryArray(
    values: Vector[Double],
    expectedSemanticError: Option[String],
) derives CanEqual

object BinaryArrayReader:
  private val ManifestFields = Set(
    "schemaVersion",
    "fixtureId",
    "argumentName",
    "fileName",
    "encoding",
    "dtype",
    "byteOrder",
    "arrayOrder",
    "shape",
    "count",
    "byteLength",
    "sha256",
    "generator",
    "expectedSemanticError",
  )
  private val SafeBasename = "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$".r
  private val Identifier = "^[a-z0-9](?:[a-z0-9._:-]{0,126}[a-z0-9])?$".r
  private val ArgumentName = "^[a-z][a-z0-9_]{0,63}$".r
  private val Sha256 = "^[0-9a-f]{64}$".r
  private val LiteralPayload = "^(?:[0-9a-f]{2})+$".r
  private val MaximumBytes = 536870912L

  private def hex(payload: Array[Byte]): String =
    payload.iterator.map(byte => f"${byte & 0xff}%02x").mkString

  private def within(root: Path, basename: String): Option[Path] =
    if !SafeBasename.matches(basename) then None
    else
      val normalizedRoot = root.toRealPath()
      val candidate = normalizedRoot.resolve(basename).normalize()
      if candidate.startsWith(normalizedRoot) then Some(candidate) else None

  private def generatorValid(generator: JsonNode): Boolean =
    if !generator.isObject then false
    else
      val fields = generator.fieldNames().asScala.toSet
      generator.path("algorithm").textValue() match
        case "numpy-pcg64" =>
          val expected =
            Set(
              "algorithm",
              "seed",
              "generatorVersion",
              "distribution",
              "parameters",
              "chunkLength",
            )
          val seed = generator.path("seed")
          val distribution = generator.path("distribution")
          val parameters = generator.path("parameters")
          val chunkLength = generator.path("chunkLength")
          fields == expected &&
          seed.isIntegralNumber &&
          seed.bigIntegerValue().signum() >= 0 &&
          generator.path("generatorVersion").textValue() == "numpy-2.5.1" &&
          Set("normal", "uniform", "lognormal", "standard_normal")
            .contains(distribution.textValue()) &&
          parameters.isObject &&
          parameters.size() <= 8 &&
          parameters.fieldNames().asScala.forall(ArgumentName.matches) &&
          parameters.elements().asScala.forall(_.isNumber) &&
          chunkLength.isIntegralNumber &&
          chunkLength.bigIntegerValue().signum() > 0 &&
          BigInt(chunkLength.bigIntegerValue()) <= BigInt(67108864)
        case "literal-ieee754-bits" =>
          val expected = Set("algorithm", "generatorVersion", "payloadHex")
          val payload = generator.path("payloadHex")
          fields == expected &&
          generator.path("generatorVersion").textValue() ==
            "s1.4x-literal-ieee754-bits-v1" &&
          payload.isTextual &&
          payload.textValue().length >= 2 &&
          payload.textValue().length <= 1024 &&
          LiteralPayload.matches(payload.textValue())
        case _ => false

  /**
   * manifest path/hash/shape를 모두 검증한 뒤에만 little-endian binary를 immutable Vector로
   * 노출한다. hash/length/endian failure는 numeric error가 아니라 exit 65 소유다.
   */
  def read(
      descriptor: JsonNode,
      fixtureRoot: Path,
      fixtureId: String,
      argumentName: String,
  ): Either[TransportError, DecodedBinaryArray] =
    val descriptorFields = descriptor.fieldNames().asScala.toSet
    val manifestName = descriptor.path("manifestFile")
    if descriptorFields != Set("kind", "manifestFile") ||
      descriptor.path("kind").textValue() != "binaryFloat64" ||
      !manifestName.isTextual
    then Left(TransportError("manifest_invalid", fixtureId = Some(fixtureId)))
    else
      val largeRoot = fixtureRoot.resolve("large")
      within(largeRoot, manifestName.textValue()) match
        case None =>
          Left(
            TransportError(
              "manifest_invalid",
              fixtureId = Some(fixtureId),
              field = Some("manifestFile"),
            )
          )
        case Some(manifestPath) =>
          try
            if !Files.isRegularFile(manifestPath) || Files.isSymbolicLink(manifestPath) then
              Left(TransportError("manifest_invalid", fixtureId = Some(fixtureId)))
            else
              val manifest = ContractDecoder.mapper.readTree(Files.readString(manifestPath))
              val fields = manifest.fieldNames().asScala.toSet
              val shape = manifest.path("shape")
              val count = manifest.path("count")
              val byteLength = manifest.path("byteLength")
              val fileName = manifest.path("fileName")
              val expectedSha = manifest.path("sha256")
              val semanticError = manifest.path("expectedSemanticError")
              val manifestFixture = manifest.path("fixtureId")
              val manifestArgument = manifest.path("argumentName")
              val generator = manifest.path("generator")
              val constantsValid =
                manifest.path("schemaVersion").textValue() == "s1.4x-binary-array-v1" &&
                  manifest.path("encoding").textValue() == "ieee754-binary64" &&
                  manifest.path("dtype").textValue() == "float64" &&
                  manifest.path("byteOrder").textValue() == "little" &&
                  manifest.path("arrayOrder").textValue() == "C"
              val exactFields =
                fields.subsetOf(ManifestFields) &&
                  (ManifestFields - "expectedSemanticError").subsetOf(fields)
              val identityValid =
                manifestFixture.isTextual &&
                  Identifier.matches(manifestFixture.textValue()) &&
                  manifestFixture.textValue() == fixtureId &&
                  manifestArgument.isTextual &&
                  ArgumentName.matches(manifestArgument.textValue()) &&
                  manifestArgument.textValue() == argumentName
              val shapeValid =
                shape.isArray &&
                  shape.size() == 1 &&
                  shape.elements().asScala.toVector.forall(node =>
                    node.isIntegralNumber && node.bigIntegerValue().signum() > 0
                  )
              val integralValid =
                count.isIntegralNumber &&
                  byteLength.isIntegralNumber
              val semanticErrorValid =
                semanticError.isMissingNode ||
                  (semanticError.isTextual &&
                    Set("input_non_finite", "research_input_invalid")
                      .contains(semanticError.textValue()))
              if !exactFields || !constantsValid || !identityValid || !generatorValid(generator) ||
                !semanticErrorValid || !shapeValid || !integralValid ||
                !fileName.isTextual || !expectedSha.isTextual ||
                !SafeBasename.matches(fileName.textValue()) ||
                !Sha256.matches(expectedSha.textValue())
              then Left(TransportError("manifest_invalid", fixtureId = Some(fixtureId)))
              else
                val expectedCount = BigInt(count.bigIntegerValue())
                val expectedLength = BigInt(byteLength.bigIntegerValue())
                val shapeCount =
                  shape.elements().asScala.toVector
                    .map(node => BigInt(node.bigIntegerValue()))
                    .foldLeft(BigInt(1))(_ * _)
                if expectedCount != shapeCount ||
                  expectedLength != expectedCount * BigInt(8) ||
                  expectedLength > BigInt(MaximumBytes)
                then Left(TransportError("manifest_invalid", fixtureId = Some(fixtureId)))
                else
                  val generatedRoot = largeRoot.resolve("generated")
                  within(generatedRoot, fileName.textValue()) match
                    case None =>
                      Left(TransportError("binary_invalid", fixtureId = Some(fixtureId)))
                    case Some(binaryPath) =>
                      if !Files.isRegularFile(binaryPath) || Files.isSymbolicLink(binaryPath) then
                        Left(TransportError("binary_invalid", fixtureId = Some(fixtureId)))
                      else
                        val payload = Files.readAllBytes(binaryPath)
                        val digest = hex(MessageDigest.getInstance("SHA-256").digest(payload))
                        if BigInt(payload.length) != expectedLength ||
                          digest != expectedSha.textValue()
                        then Left(TransportError("binary_invalid", fixtureId = Some(fixtureId)))
                        else
                          val decoded =
                            ByteBuffer
                              .wrap(payload)
                              .order(ByteOrder.LITTLE_ENDIAN)
                              .asDoubleBuffer()
                          val values = Vector.tabulate(expectedCount.toInt)(decoded.get)
                          val expectedSemanticError =
                            if semanticError.isMissingNode then None
                            else if semanticError.isTextual then Some(semanticError.textValue())
                            else Some("")
                          val containsNonFinite = values.exists(value => !value.isFinite)
                          val semanticNonFiniteAllowed =
                            containsNonFinite &&
                              expectedSemanticError.exists(
                                Set("input_non_finite", "research_input_invalid").contains
                              )
                          if containsNonFinite && !semanticNonFiniteAllowed then
                            Left(TransportError("binary_invalid", fixtureId = Some(fixtureId)))
                          else if !containsNonFinite && expectedSemanticError.nonEmpty then
                            Left(TransportError("manifest_invalid", fixtureId = Some(fixtureId)))
                          else Right(DecodedBinaryArray(values, expectedSemanticError))
          catch
            case NonFatal(_) =>
              Left(TransportError("manifest_invalid", fixtureId = Some(fixtureId)))
