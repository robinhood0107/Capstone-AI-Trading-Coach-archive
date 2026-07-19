package ai.trading.coach.s14x.core

import ai.trading.coach.s14x.shell.JsonSupport
import org.scalacheck.Gen
import org.scalacheck.Prop
import org.scalacheck.Prop.forAll
import org.scalacheck.Properties
import org.scalacheck.Test

object FrozenPropertyPlan extends Properties("s1.4x-frozen-property-plan"):
  override def overrideParameters(parameters: Test.Parameters): Test.Parameters =
    parameters.withMinSuccessfulTests(1000).withMaxDiscardRatio(0.1f).withWorkers(1)

  private val repetition = Gen.choose(0, Int.MaxValue)

  private object registered:
    def update(name: String, value: => Prop): Unit =
      val _ = FrozenPropertyPlan.property.update(name, value)

  private def right[A](value: Either[StableError, A]): Option[A] = value.toOption

  private def close(left: Double, rightValue: Double, tolerance: Double = 1.0e-10): Boolean =
    math.abs(left - rightValue) <= tolerance * math.max(1.0, math.abs(rightValue))

  registered("production.output-finite-or-stable-error") = forAll(repetition) { _ =>
    ProductionMetrics
      .simpleReturns(Vector(100.0, 101.0, 99.0))
      .fold(_ => true, _.forall(_.isFinite))
  }

  registered("simple-returns.scale-invariant") = forAll(Gen.choose(0.1, 100.0)) { scale =>
    val first = right(ProductionMetrics.simpleReturns(Vector(10.0, 11.0, 12.0)))
    val second =
      right(ProductionMetrics.simpleReturns(Vector(10.0, 11.0, 12.0).map(_ * scale)))
    first.zip(second).forall { case (left, rightValue) =>
      left.zip(rightValue).forall((one, two) => close(one, two))
    }
  }

  registered("log-returns.scale-invariant") = forAll(Gen.choose(0.1, 100.0)) { scale =>
    val first = right(ProductionMetrics.logReturns(Vector(10.0, 11.0, 12.0)))
    val second =
      right(ProductionMetrics.logReturns(Vector(10.0, 11.0, 12.0).map(_ * scale)))
    first.zip(second).forall { case (left, rightValue) =>
      left.zip(rightValue).forall((one, two) => close(one, two))
    }
  }

  registered("cumulative-return.bankruptcy-absorbing") = forAll(repetition) { _ =>
    right(ProductionMetrics.cumulativeReturn(Vector(-1.0, Double.MaxValue))).contains(-1.0)
  }

  registered("cumulative-return.manual-product-identity") = forAll(
    Gen.listOfN(5, Gen.choose(-0.5, 0.5))
  ) { values =>
    val vector = Vector.from(values)
    val manual = vector.foldLeft(1.0)((product, value) => product * (1.0 + value)) - 1.0
    right(ProductionMetrics.cumulativeReturn(vector)).exists(close(_, manual))
  }

  registered("volatility.translation-and-scale") = forAll(
    Gen.choose(-10.0, 10.0),
    Gen.choose(0.1, 10.0)
  ) { (shift, scale) =>
    val values = Vector(-0.2, 0.0, 0.1, 0.3)
    val baseline = right(ProductionMetrics.realizedVolatility(values))
    val translated = right(ProductionMetrics.realizedVolatility(values.map(_ + shift)))
    val scaled = right(ProductionMetrics.realizedVolatility(values.map(_ * scale)))
    baseline.zip(translated).forall((one, two) => close(one, two)) &&
    baseline.zip(scaled).forall((one, two) => close(one * scale, two))
  }

  registered("max-drawdown.bounds") = forAll(Gen.choose(1.0, 100.0)) { scale =>
    right(ProductionMetrics.maxDrawdown(Vector(100.0, 120.0, 60.0).map(_ * scale)))
      .exists(value => value >= -1.0 && value <= 0.0)
  }

  registered("var-hf7-observation-range") = forAll(Gen.choose(0.01, 0.99)) { confidence =>
    val values = Vector(-0.1, -0.05, 0.0, 0.05, 0.1)
    right(ProductionMetrics.historicalVar(values, confidence))
      .exists(value => value >= -0.1 && value <= 0.1)
  }

  registered("var-cvar.shift-and-positive-scale") = forAll(
    Gen.choose(-2.0, 2.0),
    Gen.choose(0.1, 10.0)
  ) { (shift, scale) =>
    val values = Vector(-0.1, -0.05, 0.0, 0.05, 0.1)
    val shiftedScaled = values.map(value => value * scale + shift)
    val original = right(ProductionMetrics.historicalVar(values, 0.8))
    val transformed = right(ProductionMetrics.historicalVar(shiftedScaled, 0.8))
    original.zip(transformed).forall((one, two) => close(one * scale + shift, two))
  }

  registered("cvar-threshold-tail") = forAll(repetition) { _ =>
    right(
      ProductionMetrics.historicalCvar(Vector(-0.1, -0.05, -0.05, -0.05, 0.1), 0.6)
    ).contains(-0.0625)
  }

  registered("expected-shortfall.permutation-invariant") = forAll(repetition) { seed =>
    val values = Vector(1.0, 2.0, 3.0, 4.0)
    val rotated = values.drop(seed % values.size) ++ values.take(seed % values.size)
    right(AdvancedRisk.historicalExpectedShortfall(values, 0.625)) ==
      right(AdvancedRisk.historicalExpectedShortfall(rotated, 0.625))
  }

  registered("realized.permutation-invariant") = forAll(repetition) { seed =>
    val values = Vector(1.0, -2.0, 2.0)
    val rotated = values.drop(seed % values.size) ++ values.take(seed % values.size)
    right(AdvancedRisk.realizedVariance(values)) == right(
      AdvancedRisk.realizedVariance(rotated)
    )
  }

  registered("realized.scale-laws") = forAll(Gen.choose(-10.0, 10.0).suchThat(_ != 0.0)) { scale =>
    val values = Vector(0.01, -0.02, 0.03)
    val rv = right(AdvancedRisk.realizedVariance(values))
    val scaledRv = right(AdvancedRisk.realizedVariance(values.map(_ * scale)))
    val rvol = right(AdvancedRisk.realizedVolatilityIntraday(values))
    val scaledRvol =
      right(AdvancedRisk.realizedVolatilityIntraday(values.map(_ * scale)))
    rv.zip(scaledRv).forall((one, two) => close(one * scale * scale, two)) &&
    rvol.zip(scaledRvol).forall((one, two) => close(one * math.abs(scale), two))
  }

  registered("lo.order-sensitive") = forAll(repetition) { _ =>
    val ordered =
      right(AdvancedRisk.loAdjustedSharpeRatio(Vector(-1.0, 0.0, 1.0, 2.0), BigInt(2)))
    val permuted =
      right(AdvancedRisk.loAdjustedSharpeRatio(Vector(-1.0, 2.0, 0.0, 1.0), BigInt(2)))
    ordered.zip(permuted).forall((one, two) => !close(one, two))
  }

  registered("psr.benchmark-equality") = forAll(Gen.choose(-4.0, 4.0)) { sharpe =>
    right(
      AdvancedRisk.probabilisticSharpeRatio(sharpe, sharpe, BigInt(6), 0.0, 3.0)
    ).contains(0.5)
  }

  private val provenance =
    TrialProvenance(
      "s1.4r-effective-trials-v1",
      "pre_registered_independent",
      BigInt(2),
      BigInt(2),
      "daily",
      "a" * 64,
      BigInt(1)
    )

  registered("dsr.benchmark-equality") = forAll(repetition) { _ =>
    right(
      AdvancedRisk.deflatedSharpeRatio(
        0.5197553442805939,
        BigInt(6),
        0.0,
        3.0,
        BigInt(2),
        1.0,
        provenance
      )
    ).exists(close(_, 0.5))
  }

  registered("dsr.provenance-count-consistency") = forAll(repetition) { _ =>
    AdvancedRisk
      .deflatedSharpeRatio(
        1.0,
        BigInt(6),
        0.0,
        3.0,
        BigInt(3),
        1.0,
        provenance
      )
      .left
      .toOption
      .contains(StableError.TrialProvenanceInvalid)
  }

  private val alternatingRealized = Vector(0.0, 2.0, 0.0, 2.0, 0.0)
  private val alternatingForecast = Vector.fill(5)(1.0)

  registered("kupiec.paired-permutation-invariant") = forAll(repetition) { _ =>
    val first = right(
      AdvancedRisk.kupiecUnconditionalCoverageTest(
        alternatingRealized,
        alternatingForecast,
        0.6
      )
    )
    val second = right(
      AdvancedRisk.kupiecUnconditionalCoverageTest(
        alternatingRealized.reverse,
        alternatingForecast.reverse,
        0.6
      )
    )
    first == second
  }

  registered("backtest.strict-loss-greater-than-var") = forAll(repetition) { _ =>
    right(
      AdvancedRisk.kupiecUnconditionalCoverageTest(
        Vector(1.0, 2.0),
        Vector(1.0, 1.0),
        0.5
      )
    ).exists(_.exceptions == 1)
  }

  registered("christoffersen.order-sensitive") = forAll(repetition) { _ =>
    val first = right(
      AdvancedRisk.christoffersenIndependenceTest(
        alternatingRealized,
        alternatingForecast
      )
    )
    val second = right(
      AdvancedRisk.christoffersenIndependenceTest(
        Vector(0.0, 0.0, 2.0, 2.0, 0.0),
        alternatingForecast
      )
    )
    first.zip(second).forall((one, two) => !close(one.statistic, two.statistic))
  }

  registered("backtest.positive-common-scaling") = forAll(Gen.choose(0.1, 10.0)) { scale =>
    val first = right(
      AdvancedRisk.kupiecUnconditionalCoverageTest(
        alternatingRealized,
        alternatingForecast,
        0.6
      )
    )
    val second = right(
      AdvancedRisk.kupiecUnconditionalCoverageTest(
        alternatingRealized.map(_ * scale),
        alternatingForecast.map(_ * scale),
        0.6
      )
    )
    first == second
  }

  registered("likelihood.record-invariants") = forAll(repetition) { _ =>
    right(
      AdvancedRisk.kupiecUnconditionalCoverageTest(
        alternatingRealized,
        alternatingForecast,
        0.6
      )
    ).exists(result =>
      result.statistic >= 0.0 &&
        result.pValue >= 0.0 &&
        result.pValue <= 1.0 &&
        result.reject == (result.pValue < result.significance)
    )
  }

  registered("conditional-coverage.component-identity") = forAll(repetition) { _ =>
    right(
      AdvancedRisk.christoffersenConditionalCoverageTest(
        alternatingRealized,
        alternatingForecast,
        0.6
      )
    ).exists(result =>
      close(
        result.statistic,
        result.unconditionalComponentStatistic + result.independenceComponentStatistic
      )
    )
  }

  registered("christoffersen.unidentifiable-transition-rejected") = forAll(repetition) { _ =>
    AdvancedRisk
      .christoffersenIndependenceTest(Vector.fill(4)(0.0), Vector.fill(4)(1.0))
      .left
      .toOption
      .contains(StableError.InsufficientSample)
  }

  registered("recursive-negative-zero-normalization") = forAll(repetition) { _ =>
    val normalized = JsonSupport.normalizeNumberTree(
      Map("vector" -> Vector(-0.0), "nested" -> Map("value" -> -0.0))
    )
    !JsonSupport.encode(normalized).contains("-0.0")
  }
