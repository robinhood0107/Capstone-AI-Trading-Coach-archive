package ai.trading.coach.s14x.benchmark

import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ArrayNode
import com.fasterxml.jackson.databind.node.ObjectNode
import java.lang.management.ManagementFactory
import java.lang.management.RuntimeMXBean
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.channels.OverlappingFileLockException
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.nio.file.attribute.FileTime
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import scala.jdk.CollectionConverters.*
import scala.util.control.NonFatal

/** 각 실제 JMH fork의 RuntimeMXBean JVM argument와 exact JDK identity를 timing 전에 exclusive JSON으로 남긴다.
  * 로컬 절대 경로와 환경값은 기록하지 않고 portable path ID와 hash만 보존한다.
  */
object JvmForkEvidence:
  private final case class UnixFileIdentity(
      device: Long,
      inode: Long,
      mode: Long,
      linkCount: Long,
      size: Long,
      mtimeNanos: Long,
      ctimeNanos: Long
  ) derives CanEqual

  private val Mapper = ObjectMapper()
  private val EvidenceDirectoryVariable = "S1_4X_EFFECTIVE_JVM_EVIDENCE_DIR"
  private val MeasurementReadyMarkerVariable = "S1_4X_MEASUREMENT_READY_MARKER"
  private val JmhTmpDirectoryVariable = "S1_4X_JMH_TMPDIR"
  private val CompileCommandPrefix = "-XX:CompileCommandFile="
  private val MaxCompileCommandBytes = 1024L * 1024L
  private val ProcessFdDirectory = Path.of("/proc/self/fd")
  private val EnvironmentNames = Vector(
    "S1_4X_BENCHMARK_CASE_ID",
    "S1_4X_BENCHMARK_PLAN",
    "S1_4X_BENCHMARK_PROFILE",
    "S1_4X_BENCHMARK_RUN_MODE",
    "S1_4X_FIXTURE_ROOT",
    EvidenceDirectoryVariable,
    JmhTmpDirectoryVariable,
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

  private def unixNumber(attributes: java.util.Map[String, Object], name: String): Long =
    attributes.get(name) match
      case number: Number => number.longValue
      case _              => throw IllegalStateException("JMH_COMPILE_COMMAND_FILE_UNSAFE")

  private def unixTime(attributes: java.util.Map[String, Object], name: String): Long =
    attributes.get(name) match
      case value: FileTime => value.to(TimeUnit.NANOSECONDS)
      case _               => throw IllegalStateException("JMH_COMPILE_COMMAND_FILE_UNSAFE")

  private def unixIdentity(path: Path, noFollowLinks: Boolean): UnixFileIdentity =
    val options: Array[LinkOption] =
      if noFollowLinks then Array(LinkOption.NOFOLLOW_LINKS)
      else Array.empty[LinkOption]
    val attributes =
      Files.readAttributes(
        path,
        "unix:dev,ino,mode,nlink,size,lastModifiedTime,ctime",
        options*
      )
    val mode = unixNumber(attributes, "mode")
    // Linux S_IFMT/S_IFREG 확인으로 symlink와 특수 파일을 같은 stat 결과에서 배제한다.
    if (mode & 0xf000L) != 0x8000L then
      throw IllegalStateException("JMH_COMPILE_COMMAND_FILE_UNSAFE")
    UnixFileIdentity(
      device = unixNumber(attributes, "dev"),
      inode = unixNumber(attributes, "ino"),
      mode = mode,
      linkCount = unixNumber(attributes, "nlink"),
      size = unixNumber(attributes, "size"),
      mtimeNanos = unixTime(attributes, "lastModifiedTime"),
      ctimeNanos = unixTime(attributes, "ctime")
    )

  private def channelBytes(channel: FileChannel): Array[Byte] =
    channel.position(0L)
    val openedSize = channel.size
    if openedSize < 0L || openedSize > MaxCompileCommandBytes then
      throw IllegalStateException("JMH_COMPILE_COMMAND_FILE_UNSAFE")
    val buffer = ByteBuffer.allocate(openedSize.toInt)
    while buffer.hasRemaining do
      if channel.read(buffer) < 0 then
        throw IllegalStateException("JMH_COMPILE_COMMAND_FILE_UNSAFE")
    val eofProbe = ByteBuffer.allocate(1)
    if channel.read(eofProbe) != -1 || channel.size != openedSize then
      throw IllegalStateException("JMH_COMPILE_COMMAND_FILE_UNSAFE")
    buffer.array()

  private def liveFileDescriptors(): Set[Int] =
    if !Files.isDirectory(ProcessFdDirectory) then
      throw IllegalStateException("JMH_COMPILE_COMMAND_FILE_UNSAFE")
    val stream = Files.list(ProcessFdDirectory)
    val observed =
      try
        stream.iterator.asScala.flatMap { entry =>
          entry.getFileName.toString.toIntOption
        }.toSet
      finally stream.close()
    // 디렉터리 열거 자체의 fd는 close 뒤 사라지므로 현재 살아 있는 항목만 남긴다.
    observed.filter { descriptor =>
      Files.exists(
        ProcessFdDirectory.resolve(descriptor.toString),
        LinkOption.NOFOLLOW_LINKS
      )
    }

  private def descriptorReferencesLockedChannel(descriptorPath: Path): Boolean =
    try
      val probe =
        FileChannel.open(descriptorPath, StandardOpenOption.READ, StandardOpenOption.WRITE)
      try
        val acquired = probe.tryLock(0L, Long.MaxValue, false)
        if acquired == null then false
        else
          acquired.release()
          false
      catch case _: OverlappingFileLockException => true
      finally probe.close()
    catch
      // JMH가 동시에 닫은 unrelated fd나 재사용된 proc entry는 후보가 아니다.
      case NonFatal(_) => false

  private def stableSingleLinkBytes(path: Path): (Array[Byte], UnixFileIdentity) =
    val before = unixIdentity(path, noFollowLinks = true)
    val descriptorsBefore = liveFileDescriptors()
    val channel =
      FileChannel.open(
        path,
        StandardOpenOption.READ,
        StandardOpenOption.WRITE,
        LinkOption.NOFOLLOW_LINKS
      )
    val lock = channel.tryLock(0L, Long.MaxValue, false)
    if lock == null then
      channel.close()
      throw IllegalStateException("JMH_COMPILE_COMMAND_FILE_UNSAFE")
    try
      val descriptorsAfter = liveFileDescriptors()
      val candidates =
        (descriptorsAfter -- descriptorsBefore).toVector
          .map(descriptor => ProcessFdDirectory.resolve(descriptor.toString))
          .filter(descriptorReferencesLockedChannel)
      if candidates.size != 1 then throw IllegalStateException("JMH_COMPILE_COMMAND_FILE_UNSAFE")
      val descriptorPath = candidates.head
      val handleBefore = unixIdentity(descriptorPath, noFollowLinks = false)
      val firstBytes = channelBytes(channel)
      val middle = unixIdentity(path, noFollowLinks = true)
      val handleMiddle = unixIdentity(descriptorPath, noFollowLinks = false)
      val secondBytes = channelBytes(channel)
      val after = unixIdentity(path, noFollowLinks = true)
      val handleAfter = unixIdentity(descriptorPath, noFollowLinks = false)
      val stable =
        before == middle &&
          middle == after &&
          before == handleBefore &&
          handleBefore == handleMiddle &&
          handleMiddle == handleAfter &&
          before.linkCount == 1L &&
          before.size == firstBytes.length.toLong &&
          firstBytes.length == secondBytes.length &&
          java.util.Arrays.equals(firstBytes, secondBytes)
      if !stable then throw IllegalStateException("JMH_COMPILE_COMMAND_FILE_UNSAFE")
      firstBytes -> before
    finally
      lock.release()
      channel.close()

  private def argumentFiles(bean: RuntimeMXBean): ArrayNode =
    val values = Mapper.createArrayNode()
    val evidenceDirectory =
      Path.of(sys.env.getOrElse(EvidenceDirectoryVariable, ""))
    val tmpDirectory = Path.of(sys.env.getOrElse(JmhTmpDirectoryVariable, ""))
    val runtimeTmpDirectory =
      Path.of(Option(System.getProperty("java.io.tmpdir")).getOrElse(""))
    val safeTmpDirectory =
      evidenceDirectory.isAbsolute &&
        tmpDirectory.isAbsolute &&
        runtimeTmpDirectory.equals(tmpDirectory) &&
        Files.isDirectory(evidenceDirectory) &&
        Files.isDirectory(tmpDirectory) &&
        !Files.isSymbolicLink(evidenceDirectory) &&
        !Files.isSymbolicLink(tmpDirectory) &&
        evidenceDirectory.toRealPath().equals(evidenceDirectory) &&
        tmpDirectory.toRealPath().equals(tmpDirectory) &&
        evidenceDirectory.getParent != null &&
        tmpDirectory.equals(evidenceDirectory.getParent.resolve("jmh-tmp"))
    if !safeTmpDirectory then throw IllegalStateException("JMH_TMP_DIRECTORY_UNSAFE")
    bean.getInputArguments.asScala.zipWithIndex.foreach { case (argument, index) =>
      if argument.startsWith(CompileCommandPrefix) then
        val path = Path.of(argument.stripPrefix(CompileCommandPrefix))
        if (
            !path.isAbsolute ||
            !tmpDirectory.equals(path.getParent) ||
            Files.isSymbolicLink(path)
          )
        then throw IllegalStateException("JMH_COMPILE_COMMAND_FILE_UNSAFE")
        val (bytes, fileIdentity) = stableSingleLinkBytes(path)
        val identity = Mapper.createObjectNode()
        identity.put("argumentIndex", index)
        identity.put("argumentPrefix", CompileCommandPrefix)
        identity.put("pathId", "JMH_COMPILE_COMMAND_FILE")
        identity.put("sha256", sha256(bytes))
        val file = Mapper.createObjectNode()
        file.put("device", fileIdentity.device)
        file.put("inode", fileIdentity.inode)
        file.put("mode", fileIdentity.mode)
        file.put("linkCount", fileIdentity.linkCount)
        file.put("size", fileIdentity.size)
        file.put("mtimeNs", fileIdentity.mtimeNanos)
        file.put("ctimeNs", fileIdentity.ctimeNanos)
        identity.set[ObjectNode]("fileIdentity", file)
        values.add(identity): Unit
    }
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
    value.set[ArrayNode]("inputArgumentFiles", argumentFiles(bean))
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
