package ai.trading.coach.s14x.benchmark

import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ArrayNode
import com.fasterxml.jackson.databind.node.ObjectNode
import java.lang.management.ManagementFactory
import java.lang.management.RuntimeMXBean
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.security.MessageDigest
import scala.jdk.CollectionConverters.*
import scala.util.control.NonFatal

/** 각 실제 JMH fork의 RuntimeMXBean JVM argument와 exact JDK identity를 timing 전에 exclusive JSON으로 남긴다.
  * 로컬 절대 경로와 환경값은 기록하지 않고 portable path ID와 hash만 보존한다.
  */
object JvmForkEvidence:
  private val Mapper = ObjectMapper()
  private val EvidenceDirectoryVariable = "S1_4X_EFFECTIVE_JVM_EVIDENCE_DIR"
  private val MeasurementReadyMarkerVariable = "S1_4X_MEASUREMENT_READY_MARKER"
  private val EnvironmentNames = Vector(
    "S1_4X_BENCHMARK_CASE_ID",
    "S1_4X_BENCHMARK_PLAN",
    "S1_4X_BENCHMARK_PROFILE",
    "S1_4X_BENCHMARK_RUN_MODE",
    "S1_4X_FIXTURE_ROOT",
    EvidenceDirectoryVariable,
    MeasurementReadyMarkerVariable,
    "S1_4X_SCALA_WORKSPACE",
    "COURSIER_CACHE",
    "COURSIER_CONFIG_DIR",
    "SCALA_CLI_HOME",
    "SCALA_CLI_CONFIG",
    "XDG_CONFIG_HOME"
  )
  private val AmbientJvmOptionNames =
    Vector("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS")
  private val StablePropertyNames = Vector(
    "java.runtime.version",
    "java.vendor",
    "java.vm.name",
    "java.specification.version"
  )

  private def sha256(bytes: Array[Byte]): String =
    MessageDigest
      .getInstance("SHA-256")
      .digest(bytes)
      .map(value => f"${value & 0xff}%02x")
      .mkString

  private def sha256(value: String): String =
    sha256(value.getBytes(StandardCharsets.UTF_8))

  private def fileSha256(path: Path): String =
    sha256(Files.readAllBytes(path))

  private def canonicalPairs(values: Vector[(String, String)]): String =
    values.sortBy(_._1).map { case (key, value) => s"$key=$value\n" }.mkString

  private def environmentHash: String =
    sha256(
      canonicalPairs(
        EnvironmentNames.map(name => name -> sys.env.get(name).fold("UNSET")(_ => "SET"))
      )
    )

  private def stableProperties: Vector[(String, String)] =
    StablePropertyNames.map(name => name -> Option(System.getProperty(name)).getOrElse("UNSET"))

  private def ambientJvmOptions: Vector[(String, String)] =
    AmbientJvmOptionNames.map(name => name -> sys.env.getOrElse(name, "UNSET"))

  private def objectNode(values: Vector[(String, String)]): ObjectNode =
    values.foldLeft(Mapper.createObjectNode()) { case (node, (key, value)) =>
      node.put(key, value)
    }

  private def arguments(bean: RuntimeMXBean): ArrayNode =
    val values = Mapper.createArrayNode()
    bean.getInputArguments.asScala.foreach(values.add)
    values

  private def payload(bean: RuntimeMXBean, javaExecutable: Path): ObjectNode =
    val value = Mapper.createObjectNode()
    value.put("schemaVersion", "s1.4x-scala-jvm-fork-raw-evidence-v1")
    value.put("forkProcessId", bean.getPid)
    value.put("runtimeStartTimeEpochMillis", bean.getStartTime)
    value.put("javaExecutablePathId", "TEMURIN_25_0_3_9_LTS/bin/java")
    value.put("javaExecutableSha256", fileSha256(javaExecutable))
    value.put("runtimeVersion", System.getProperty("java.runtime.version"))
    value.put("vendor", System.getProperty("java.vendor"))
    value.put("javaHomePathId", "TEMURIN_25_0_3_9_LTS")
    value.set[ArrayNode]("inputArguments", arguments(bean))
    val properties = stableProperties
    val ambient = ambientJvmOptions
    value.set[ObjectNode]("stableSystemProperties", objectNode(properties))
    value.set[ObjectNode]("ambientJvmOptionVariables", objectNode(ambient))
    value.put("systemPropertiesSha256", sha256(canonicalPairs(properties)))
    value.put("environmentAllowlistSha256", environmentHash)
    value.put(
      "runtimeClasspathSha256",
      sha256(Option(System.getProperty("java.class.path")).getOrElse(""))
    )
    value

  private def measurementReadyPayload(
      plan: Path,
      caseId: String,
      profile: String,
      runMode: String
  ): Array[Byte] =
    val value = Mapper.createObjectNode()
    value.put("schemaVersion", "s1.4x-scala-measurement-ready-v1")
    value.put("benchmarkPlanSha256", fileSha256(plan))
    value.put("caseId", caseId)
    value.put("profileId", profile)
    value.put("runMode", runMode)
    value.put("setupStatus", "PASS")
    value.put("markerCardinality", 1)
    (Mapper.writeValueAsString(value) + "\n").getBytes(StandardCharsets.UTF_8)

  /** Evidence directory가 없거나 unsafe하면 false를 반환해 benchmark setup이 exit 70으로 닫히게 한다. */
  def record(): Boolean =
    try
      sys.env.get(EvidenceDirectoryVariable).exists { configured =>
        val directory = Path.of(configured)
        val javaExecutable = Path.of(System.getProperty("java.home"), "bin", "java")
        val bean = ManagementFactory.getRuntimeMXBean
        val safeDirectory =
          directory.isAbsolute &&
            Files.isDirectory(directory) &&
            !Files.isSymbolicLink(directory)
        val safeJava =
          Files.isRegularFile(javaExecutable) &&
            !Files.isSymbolicLink(javaExecutable)
        if safeDirectory && safeJava then
          val output = directory.resolve(s"jvm-fork-${bean.getPid}.json")
          Files.writeString(
            output,
            Mapper.writeValueAsString(payload(bean, javaExecutable)) + "\n",
            StandardCharsets.UTF_8,
            StandardOpenOption.CREATE_NEW,
            StandardOpenOption.WRITE
          )
          true
        else false
      }
    catch case NonFatal(_) => false

  /** fixture/plan decode와 첫 numeric 강제 평가가 끝난 뒤에만 deterministic marker를 최초 1회 생성한다. 후속 fork는 같은
    * bytes를 확인하므로 compile/setup 실패가 measurement로 승격되지 않는다.
    */
  def markMeasurementReady(): Boolean =
    try
      val inputs =
        for
          configured <- sys.env.get(MeasurementReadyMarkerVariable)
          planValue <- sys.env.get("S1_4X_BENCHMARK_PLAN")
          caseId <- sys.env.get("S1_4X_BENCHMARK_CASE_ID")
          profile <- sys.env.get("S1_4X_BENCHMARK_PROFILE")
          runMode <- sys.env.get("S1_4X_BENCHMARK_RUN_MODE")
        yield (
          Path.of(configured),
          Path.of(planValue),
          caseId,
          profile,
          runMode
        )
      inputs.exists { case (marker, plan, caseId, profile, runMode) =>
        val safe =
          marker.isAbsolute &&
            plan.isAbsolute &&
            Files.isRegularFile(plan) &&
            !Files.isSymbolicLink(plan) &&
            Files.isDirectory(marker.getParent) &&
            !Files.isSymbolicLink(marker.getParent) &&
            caseId.matches("^[a-z0-9][a-z0-9._/-]{0,191}$") &&
            Set("A", "B", "C").contains(profile) &&
            Set("smoke", "qualification", "full").contains(runMode)
        if safe then
          val payload = measurementReadyPayload(plan, caseId, profile, runMode)
          if Files.exists(marker) then
            Files.isRegularFile(marker) &&
            !Files.isSymbolicLink(marker) &&
            java.util.Arrays.equals(Files.readAllBytes(marker), payload)
          else
            Files.write(
              marker,
              payload,
              StandardOpenOption.CREATE_NEW,
              StandardOpenOption.WRITE
            )
            true
        else false
      }
    catch case NonFatal(_) => false
