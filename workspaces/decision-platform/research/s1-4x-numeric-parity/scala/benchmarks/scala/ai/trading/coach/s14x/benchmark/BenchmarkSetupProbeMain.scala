package ai.trading.coach.s14x.benchmark

/**
 * 측정 marker 전에 wrapper가 같은 frozen case의 plan/fixture/setup 강제 평가를 확인한다.
 * 성공 시에만 0을 반환하며 malformed 외부 입력은 BenchmarkInvocation의 fail-closed 경계를 따른다.
 */
object BenchmarkSetupProbeMain:
  /** wrapper가 넘긴 단일 family의 frozen setup을 timing 전 강제 평가하며 잘못된 argv는 exit 64다. */
  def main(arguments: Array[String]): Unit =
    arguments.toVector match
      case Vector(expectedFamily) =>
        BenchmarkInvocation.fromEnvironment(expectedFamily).requireValidSetup()
      case _ =>
        System.exit(64)
