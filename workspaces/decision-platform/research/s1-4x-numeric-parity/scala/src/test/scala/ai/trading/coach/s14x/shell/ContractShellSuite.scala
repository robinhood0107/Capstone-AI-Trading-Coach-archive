package ai.trading.coach.s14x.shell

import ai.trading.coach.s14x.core.StableError
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import munit.FunSuite

final class ContractShellSuite extends FunSuite:
  private val s1Root =
    Path
      .of("workspaces/decision-platform/research/s1-4x-numeric-parity")
      .toAbsolutePath
      .normalize()

  private def sha256(payload: Array[Byte]): String =
    MessageDigest
      .getInstance("SHA-256")
      .digest(payload)
      .iterator
      .map(byte => f"${byte & 0xff}%02x")
      .mkString

  private def binaryFixture(
      root: Path,
      argumentName: String,
      expectedSemanticError: Option[String] = None,
  ): Unit =
    val large = Files.createDirectories(root.resolve("large"))
    val generated = Files.createDirectories(large.resolve("generated"))
    val payload =
      ByteBuffer
        .allocate(8)
        .order(ByteOrder.LITTLE_ENDIAN)
        .putDouble(expectedSemanticError.fold(1.0)(_ => Double.NaN))
        .array()
    val _ = Files.write(generated.resolve("value.f64le"), payload)
    val semantic = expectedSemanticError.fold("")(value => s""","expectedSemanticError":"$value"""")
    val manifest =
      s"""{"schemaVersion":"s1.4x-binary-array-v1","fixtureId":"binary-fixture","argumentName":"$argumentName","fileName":"value.f64le","encoding":"ieee754-binary64","dtype":"float64","byteOrder":"little","arrayOrder":"C","shape":[1],"count":1,"byteLength":8,"sha256":"${sha256(payload)}","generator":{"algorithm":"literal-ieee754-bits","generatorVersion":"s1.4x-literal-ieee754-bits-v1","payloadHex":"000000000000f03f"}$semantic}"""
    val _ = Files.writeString(large.resolve("value.manifest.json"), manifest)

  test("strict parser는 duplicate key와 decimal exact integer를 구분한다"):
    assert(
      ContractDecoder
        .decode(
          """{"schemaVersion":"s1.4x-request-v1","requestId":"a","requestId":"b","cases":[]}"""
        )
        .isLeft
    )
    val decimal =
      """{"schemaVersion":"s1.4x-request-v1","requestId":"a","cases":[{"fixtureId":"f","functionId":"cagr","arguments":{"prices":[100,101],"periods_per_year":1000.0}}]}"""
    val request = ContractDecoder.decode(decimal)
    request match
      case Right(value) =>
        val result = CandidateRunner.execute(value, Files.createTempDirectory("s14x-fixtures"))
        result match
          case Right(batch) =>
            assertEquals(batch.results.size, 1)
            val first = batch.results.take(1).foldLeft(Option.empty[CandidateCaseResult]) {
              (_, item) => Some(item)
            }
            assertEquals(
              first.flatMap(_.errorCode),
              Some(StableError.PeriodsPerYearInvalid.code),
            )
          case Left(error) => fail(s"semantic case는 transport error가 아니어야 한다: ${error.code}")
      case Left(error) => fail(s"request envelope는 valid여야 한다: ${error.code}")

  test("recursive normalizer는 nested -0.0을 positive zero로 바꾼다"):
    val normalized = JsonSupport.normalizeNumberTree(
      Map("vector" -> Vector(-0.0, 1.0), "nested" -> Map("value" -> -0.0))
    )
    val encoded = JsonSupport.encode(normalized)
    assert(!encoded.contains("-0.0"))

  test("stable error registry는 19+13 exact code를 가진다"):
    assertEquals(StableError.values.length, 32)
    assertEquals(StableError.values.map(_.code).toSet.size, 32)

  test("frozen canonical request는 20개 function argument contract를 모두 total lookup한다"):
    val request =
      Files.readString(
        s1Root.resolve("contract/fixtures/small/canonical-inputs.v1.json")
      )
    assertEquals(
      ContractDecoder.decode(request).map(_.cases.map(_.functionId).distinct),
      Right(FunctionId.values.toVector),
    )

  test("binary manifest argument identity 불일치는 transport failure다"):
    val root = Files.createTempDirectory("s14x-binary-identity")
    binaryFixture(root, "returns")
    val descriptor =
      ContractDecoder.mapper.readTree(
        """{"kind":"binaryFloat64","manifestFile":"value.manifest.json"}"""
      )
    val result = BinaryArrayReader.read(descriptor, root, "binary-fixture", "prices")
    assertEquals(
      result.left.toOption.map(_.code),
      Some("manifest_invalid"),
    )

  test("정상 hash의 non-finite binary는 동결 semantic error로 전달된다"):
    val root = Files.createTempDirectory("s14x-binary-nonfinite")
    binaryFixture(root, "returns", Some("input_non_finite"))
    val request =
      ContractDecoder.decode(
        """{"schemaVersion":"s1.4x-request-v1","requestId":"binary-semantic","cases":[{"fixtureId":"binary-fixture","functionId":"cumulative_return","arguments":{"returns":{"kind":"binaryFloat64","manifestFile":"value.manifest.json"}}}]}"""
      )
    val executed = request.flatMap(value => CandidateRunner.execute(value, root))
    assertEquals(
      executed.toOption.flatMap(_.results.take(1).flatMap(_.errorCode).headOption),
      Some(StableError.InputNonFinite.code),
    )

  test("candidate output은 기존 파일을 덮어쓰지 않는다"):
    val fixtureRoot = Files.createTempDirectory("s14x-output-fixtures")
    val request = fixtureRoot.resolve("request.json")
    val output = fixtureRoot.resolve("result.json")
    val original = "do-not-overwrite\n"
    val _ = Files.writeString(
      request,
      """{"schemaVersion":"s1.4x-request-v1","requestId":"exclusive-output","cases":[{"fixtureId":"simple","functionId":"simple_returns","arguments":{"prices":[100,101]}}]}""",
    )
    val _ = Files.writeString(output, original)
    val exit = Main.run(
      Vector(
        "--request",
        request.toAbsolutePath.toString,
        "--fixture-root",
        fixtureRoot.toAbsolutePath.toString,
        "--output",
        output.toAbsolutePath.toString,
      )
    )
    assertEquals(exit, 70)
    assertEquals(Files.readString(output), original)
