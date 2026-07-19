package ai.trading.coach.s14x.shell

import java.nio.charset.StandardCharsets
import java.nio.channels.FileChannel
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import scala.util.control.NonFatal

object Main:
  private final case class Cli(request: Path, fixtureRoot: Path, output: Path)

  private def parseCli(arguments: Vector[String]): Either[TransportError, Cli] =
    val pairs =
      if arguments.size == 6 then arguments.grouped(2).toVector else Vector.empty
    val options = pairs.collect { case Vector(name, value) => name -> value }.toMap
    val expected = Set("--request", "--fixture-root", "--output")
    if options.keySet != expected then Left(TransportError("request_invalid"))
    else
      val request = Path.of(options.getOrElse("--request", ""))
      val fixtureRoot = Path.of(options.getOrElse("--fixture-root", ""))
      val output = Path.of(options.getOrElse("--output", ""))
      if !request.isAbsolute || !fixtureRoot.isAbsolute || !output.isAbsolute then
        Left(TransportError("request_invalid"))
      else Right(Cli(request, fixtureRoot, output))

  private def atomicWrite(output: Path, payload: Array[Byte]): Unit =
    val parent = output.getParent
    Files.createDirectories(parent)
    val temporary =
      Files.createTempFile(parent, "." + output.getFileName.toString + ".", ".tmp")
    try
      val _ = Files.write(temporary, payload)
      val channel = FileChannel.open(temporary, StandardOpenOption.WRITE)
      try channel.force(true)
      finally channel.close()
      // 같은 directory의 hard-link publish는 기존 output을 절대 교체하지 않고 원자적으로 보인다.
      val _ = Files.createLink(output, temporary)
    finally
      val _ = Files.deleteIfExists(temporary)

  private def emit(error: TransportError): Unit =
    System.err.write(JsonSupport.bytes(JsonSupport.transportNode(error)))
    System.err.flush()

  private[shell] def run(arguments: Vector[String]): Int =
    parseCli(arguments) match
      case Left(error) =>
        emit(error)
        64
      case Right(cli) =>
        try
          if !Files.isRegularFile(cli.request) || !Files.isDirectory(cli.fixtureRoot) then
            emit(TransportError("request_invalid"))
            64
          else
            val requestText = Files.readString(cli.request, StandardCharsets.UTF_8)
            ContractDecoder.decode(requestText) match
              case Left(error) =>
                emit(error)
                64
              case Right(request) =>
                CandidateRunner.execute(request, cli.fixtureRoot) match
                  case Left(error) =>
                    emit(error)
                    65
                  case Right(batch) =>
                    atomicWrite(cli.output, JsonSupport.bytes(JsonSupport.batchNode(batch)))
                    0
        catch
          case NonFatal(_) =>
            emit(TransportError("internal_error"))
            70

  /** shell의 유일한 process exit boundary이며 stdout은 항상 비워 둔다. */
  def main(arguments: Array[String]): Unit =
    System.exit(run(arguments.toVector))
