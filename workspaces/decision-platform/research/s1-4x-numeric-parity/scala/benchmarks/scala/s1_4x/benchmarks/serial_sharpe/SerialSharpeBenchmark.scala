package s1_4x.benchmarks.serial_sharpe

import ai.trading.coach.s14x.benchmark.BenchmarkInvocation
import org.openjdk.jmh.annotations.Benchmark
import org.openjdk.jmh.annotations.Level
import org.openjdk.jmh.annotations.Scope
import org.openjdk.jmh.annotations.Setup
import org.openjdk.jmh.annotations.State
import org.openjdk.jmh.infra.Blackhole

@State(Scope.Benchmark)
class SerialSharpeBenchmark:
  private lazy val invocation = BenchmarkInvocation.fromEnvironment("serial-sharpe")

  @Setup(Level.Trial)
  def setup(): Unit = invocation.requireValidSetup()

  @Benchmark
  def benchmark(blackhole: Blackhole): Unit = blackhole.consume(invocation.run())
