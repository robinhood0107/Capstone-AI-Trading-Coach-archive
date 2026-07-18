package ai.trading.coach.s14x.shell

import ai.trading.coach.s14x.core.FrozenPropertyPlan
import ai.trading.coach.s14x.core.StableError
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.node.ArrayNode
import com.fasterxml.jackson.databind.node.ObjectNode
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.security.MessageDigest
import java.time.Instant
import org.scalacheck.Prop
import org.scalacheck.Test
import org.scalacheck.rng.Seed
import scala.jdk.CollectionConverters.*

/** frozen property를 직접 실행하고 ScalaCheck Result에서 얻은 count만 evidence로 기록한다. wrapper나 integration
  * gate가 성공 횟수를 추정하거나 console text를 다시 해석하지 않는다.
  */
object PropertyEvidenceMain:
  private val Implementation = "scala-3.8.4-jvm25"
  private val Sha256Pattern = "^[0-9a-f]{64}$".r
  private val PropertyPrefix = "s1.4x-frozen-property-plan."

  private final case class Cli(
      outputDir: Path,
      s1Root: Path,
      profile: String,
      commandArgvSha256: String,
      runnerPath: Path
  )

  private final case class SeedExecution(
      seedIndex: Int,
      successful: Int,
      discarded: Int,
      attempted: Int,
      seed: Long,
      replayToken: String,
      shrinks: Int,
      status: String
  )

  private final case class PropertyExecution(
      propertyId: String,
      successful: Int,
      discarded: Int,
      attempted: Int,
      shrinks: Int,
      seedExecutions: Vector[SeedExecution],
      status: String
  )

  private def parseCli(arguments: Vector[String]): Either[String, Cli] =
    val pairs =
      if arguments.size == 10 then arguments.grouped(2).toVector else Vector.empty
    val options = pairs.collect { case Vector(name, value) => name -> value }.toMap
    val expected =
      Set("--output-dir", "--s1-root", "--profile", "--command-argv-sha256", "--runner-path")
    if options.keySet != expected then Left("property evidence CLI mismatch")
    else
      val outputDir = Path.of(options.getOrElse("--output-dir", "")).toAbsolutePath.normalize()
      val s1Root = Path.of(options.getOrElse("--s1-root", "")).toAbsolutePath.normalize()
      val profile = options.getOrElse("--profile", "")
      val commandSha = options.getOrElse("--command-argv-sha256", "")
      val runnerPath =
        Path.of(options.getOrElse("--runner-path", "")).toAbsolutePath.normalize()
      if !Set("A", "B", "C").contains(profile) then Left("unknown Scala profile")
      else if !Sha256Pattern.matches(commandSha) then Left("command argv SHA mismatch")
      else if !Files.isDirectory(s1Root) ||
        !Files.isRegularFile(runnerPath) ||
        Files.isSymbolicLink(runnerPath)
      then Left("property evidence input path mismatch")
      else Right(Cli(outputDir, s1Root, profile, commandSha, runnerPath))

  private def sha256(path: Path): String =
    sha256Bytes(Files.readAllBytes(path))

  private def sha256Bytes(payload: Array[Byte]): String =
    MessageDigest
      .getInstance("SHA-256")
      .digest(payload)
      .iterator
      .map(byte => f"${byte & 0xff}%02x")
      .mkString

  private def sourceClosure(scalaRoot: Path): String =
    val roots =
      Vector(
        scalaRoot.resolve("project.scala"),
        scalaRoot.resolve("selected-profile.scala"),
        scalaRoot.resolve("src/main/scala"),
        scalaRoot.resolve("src/test/scala"),
        scalaRoot.resolve("benchmarks")
      )
    val files = roots
      .flatMap { root =>
        if Files.isRegularFile(root) then Vector(root)
        else if Files.isDirectory(root) then
          val stream = Files.walk(root)
          try stream.iterator().asScala.filter(Files.isRegularFile(_)).toVector
          finally stream.close()
        else Vector.empty
      }
      .sortBy(path => scalaRoot.relativize(path).toString.replace('\\', '/'))
    val digest = MessageDigest.getInstance("SHA-256")
    files.foreach { path =>
      val relative =
        scalaRoot.relativize(path).toString.replace('\\', '/').getBytes(StandardCharsets.UTF_8)
      digest.update(relative)
      digest.update(0.toByte)
      digest.update(Files.readAllBytes(path))
      digest.update(0.toByte)
    }
    digest.digest().iterator.map(byte => f"${byte & 0xff}%02x").mkString

  private def propertyId(name: String): String =
    if name.startsWith(PropertyPrefix) then name.drop(PropertyPrefix.length) else name

  private def shrinks(result: Test.Result): Int =
    result.status match
      case failed: Test.Failed           => failed.args.map(_.shrinks).sum
      case exception: Test.PropException => exception.args.map(_.shrinks).sum
      case proved: Test.Proved           => proved.args.map(_.shrinks).sum
      case _                             => 0

  private def executeSeed(
      property: Prop,
      seedValue: Long,
      seedIndex: Int,
      minimumSuccessful: Int
  ): SeedExecution =
    val seed = Seed(seedValue)
    val parameters =
      Test.Parameters.default
        .withMinSuccessfulTests(minimumSuccessful)
        .withMaxDiscardRatio(0.1f)
        .withWorkers(1)
        .withInitialSeed(seed)
    val result = Test.check(parameters, property)
    val attempted = result.succeeded + result.discarded
    val status =
      if result.passed &&
        result.succeeded == minimumSuccessful &&
        attempted == result.succeeded + result.discarded
      then "PASS"
      else "FAIL"
    SeedExecution(
      seedIndex,
      result.succeeded,
      result.discarded,
      attempted,
      seedValue,
      seed.toBase64,
      shrinks(result),
      status
    )

  private def executeProperty(
      name: String,
      property: Prop,
      seeds: Vector[Long],
      minimumSuccessfulPerSeed: Int
  ): PropertyExecution =
    val seedExecutions = seeds.zipWithIndex.map { case (seed, index) =>
      executeSeed(property, seed, index, minimumSuccessfulPerSeed)
    }
    val successful = seedExecutions.map(_.successful).sum
    val discarded = seedExecutions.map(_.discarded).sum
    val attempted = seedExecutions.map(_.attempted).sum
    val shrinkCount = seedExecutions.map(_.shrinks).sum
    val status =
      if seedExecutions.size == 24 &&
        seedExecutions.forall(_.status == "PASS") &&
        successful == seedExecutions.size * minimumSuccessfulPerSeed &&
        discarded <= 100 &&
        attempted == successful + discarded
      then "PASS"
      else "FAIL"
    PropertyExecution(
      propertyId(name),
      successful,
      discarded,
      attempted,
      shrinkCount,
      seedExecutions,
      status
    )

  private def seedNode(value: SeedExecution): ObjectNode =
    val node = ContractDecoder.mapper.createObjectNode()
    node.put("seedIndex", value.seedIndex)
    node.put("originalSeed", value.seed)
    node.put("successfulTests", value.successful)
    node.put("discardedTests", value.discarded)
    node.put("attemptedTests", value.attempted)
    node.put("replayToken", value.replayToken)
    node.put("shrinks", value.shrinks)
    node.put("status", value.status)
    node

  private def propertyNode(value: PropertyExecution, detailed: Boolean): ObjectNode =
    val node = ContractDecoder.mapper.createObjectNode()
    node.put("propertyId", value.propertyId)
    node.put("successfulTests", value.successful)
    node.put("discardedTests", value.discarded)
    if detailed then
      val _ = node.put("attemptedTests", value.attempted)
      val _ = node.put("shrinks", value.shrinks)
      val _ = node.put("seedCount", value.seedExecutions.size)
      val seeds = value.seedExecutions.foldLeft(ContractDecoder.mapper.createArrayNode()) {
        (array, execution) => array.add(seedNode(execution))
      }
      val _ = node.set[ArrayNode]("seedExecutions", seeds)
    node.put("status", value.status)
    node

  private def propertyArray(
      executions: Vector[PropertyExecution],
      detailed: Boolean
  ): ArrayNode =
    executions.foldLeft(ContractDecoder.mapper.createArrayNode()) { (array, execution) =>
      array.add(propertyNode(execution, detailed))
    }

  private def registryReport(s1Root: Path): ObjectNode =
    val functionRegistry =
      ContractDecoder.mapper.readTree(
        Files.readString(s1Root.resolve("contract/function-registry.v1.json"))
      )
    val errorRegistry =
      ContractDecoder.mapper.readTree(
        Files.readString(s1Root.resolve("contract/error-registry.v1.json"))
      )
    val expectedFunctions =
      functionRegistry
        .path("entries")
        .elements()
        .asScala
        .toVector
        .map(_.path("functionId").textValue())
    val actualFunctions = FunctionId.values.toVector.map(_.wire)
    val functionStatus = expectedFunctions == actualFunctions && expectedFunctions.size == 20
    val expectedErrors = errorRegistry.path("entries").elements().asScala.toVector
    val actualErrors = StableError.values.toVector.map(_.code)
    val errorCodes = expectedErrors.map(_.path("code").textValue())
    val errorStatus = errorCodes == actualErrors && errorCodes.size == 32

    val root = ContractDecoder.mapper.createObjectNode()
    root.put("schemaVersion", "s1.4x-candidate-registry-coverage-v1")
    root.put("implementation", Implementation)
    val functions = expectedFunctions.foldLeft(ContractDecoder.mapper.createArrayNode()) {
      (array, functionId) =>
        val entry = ContractDecoder.mapper.createObjectNode()
        entry.put("functionId", functionId)
        entry.put("status", if actualFunctions.contains(functionId) then "PASS" else "FAIL")
        array.add(entry)
    }
    val errors = expectedErrors.foldLeft(ContractDecoder.mapper.createArrayNode()) {
      (array, expected) =>
        val entry = ContractDecoder.mapper.createObjectNode()
        val code = expected.path("code").textValue()
        entry.put("errorCode", code)
        entry.put("track", expected.path("track").textValue())
        entry.put("verificationMode", expected.path("verificationMode").textValue())
        entry.put("status", if actualErrors.contains(code) then "PASS" else "FAIL")
        array.add(entry)
    }
    root.set[ArrayNode]("functions", functions)
    root.set[ArrayNode]("errors", errors)
    root.put("status", if functionStatus && errorStatus then "PASS" else "FAIL")
    root

  private def writeExclusive(path: Path, node: JsonNode): Unit =
    val payload =
      (ContractDecoder.mapper.writeValueAsString(node) + "\n").getBytes(StandardCharsets.UTF_8)
    val _ = Files.write(path, payload, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)

  private def run(cli: Cli): Int =
    val startedAt = Instant.now().toString
    val scalaRoot = cli.s1Root.resolve("scala")
    val propertyPlan = cli.s1Root.resolve("contract/property-plan.v1.json")
    val seedCorpus = cli.s1Root.resolve("contract/fixtures/property/property-seeds.v1.json")
    val seedRoot = ContractDecoder.mapper.readTree(Files.readString(seedCorpus))
    val seeds = seedRoot.path("seeds").elements().asScala.toVector.map(_.longValue())
    val registered = FrozenPropertyPlan.properties.toVector
    val propertyPlanRoot =
      ContractDecoder.mapper.readTree(Files.readString(propertyPlan))
    val expectedIds =
      propertyPlanRoot
        .path("properties")
        .elements()
        .asScala
        .toVector
        .map(_.path("propertyId").textValue())
    val registeredIds = registered.map((name, _) => propertyId(name))
    val minimumSuccessful = propertyPlanRoot.path("minimumSuccessfulPerProperty").intValue()
    val maximumDiscarded = propertyPlanRoot.path("maximumDiscardedPerProperty").intValue()
    val minimumSuccessfulPerSeed =
      if seeds.nonEmpty then (minimumSuccessful + seeds.size - 1) / seeds.size else 0
    val closureValid =
      seedRoot.path("schemaVersion").textValue() == "s1.4x-property-seeds-v1" &&
        seedRoot.path("generator").textValue() == "numpy-pcg64" &&
        seedRoot.path("generatorVersion").textValue() == "numpy-2.5.1" &&
        propertyPlanRoot.path("seedCount").intValue() == 24 &&
        minimumSuccessful == 1000 &&
        maximumDiscarded == 100 &&
        minimumSuccessfulPerSeed == 42 &&
        seeds.size == 24 &&
        seeds.distinct.size == seeds.size &&
        expectedIds.size == 25 &&
        registeredIds == expectedIds &&
        registeredIds.distinct.size == registeredIds.size
    val executions =
      if closureValid then
        registered.map { case (name, property) =>
          executeProperty(name, property, seeds, minimumSuccessfulPerSeed)
        }
      else Vector.empty
    val propertyStatus =
      closureValid &&
        executions.size == 25 &&
        executions.forall(_.status == "PASS")
    val finishedAt = Instant.now().toString
    val exitCode = if propertyStatus then 0 else 1
    val propertyPlanSha = sha256(propertyPlan)

    val propertyReport = ContractDecoder.mapper.createObjectNode()
    propertyReport.put("schemaVersion", "s1.4x-candidate-property-coverage-v1")
    propertyReport.put("implementation", Implementation)
    propertyReport.put("propertyPlanSha256", propertyPlanSha)
    propertyReport.set[ArrayNode]("properties", propertyArray(executions, detailed = false))
    propertyReport.put("status", if propertyStatus then "PASS" else "FAIL")

    val executionReport = ContractDecoder.mapper.createObjectNode()
    executionReport.put("schemaVersion", "s1.4x-candidate-property-execution-v1")
    executionReport.put("implementation", Implementation)
    executionReport.put("propertyPlanSha256", propertyPlanSha)
    executionReport.put("seedCorpusSha256", sha256(seedCorpus))
    executionReport.put("seedCount", seeds.size)
    executionReport.put("minimumSuccessfulPerSeed", minimumSuccessfulPerSeed)
    executionReport.put("framework", "scala-check-1.19.0")
    executionReport.put("toolchainProfile", cli.profile)
    executionReport.put("commandArgvSha256", cli.commandArgvSha256)
    executionReport.put("runnerSha256", sha256(cli.runnerPath))
    executionReport.put("sourceClosureSha256", sourceClosure(scalaRoot))
    executionReport.put("startedAt", startedAt)
    executionReport.put("finishedAt", finishedAt)
    executionReport.put("exitCode", exitCode)
    executionReport.set[ArrayNode]("properties", propertyArray(executions, detailed = true))
    executionReport.put("status", if propertyStatus then "PASS" else "FAIL")

    Files.createDirectories(cli.outputDir)
    writeExclusive(cli.outputDir.resolve("scala-property-report.v1.json"), propertyReport)
    writeExclusive(
      cli.outputDir.resolve("scala-registry-report.v1.json"),
      registryReport(cli.s1Root)
    )
    writeExclusive(
      cli.outputDir.resolve("scala-property-execution-evidence.v1.json"),
      executionReport
    )
    exitCode

  /** frozen 24-seed property plan을 실행해 새 output directory에 byte-bound evidence를 배타 생성한다. */
  def main(arguments: Array[String]): Unit =
    val exitCode =
      parseCli(arguments.toVector) match
        case Left(message) =>
          System.err.println(message)
          64
        case Right(cli) => run(cli)
    System.exit(exitCode)
