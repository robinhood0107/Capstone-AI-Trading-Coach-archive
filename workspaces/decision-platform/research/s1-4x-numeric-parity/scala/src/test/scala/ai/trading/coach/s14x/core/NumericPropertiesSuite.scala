package ai.trading.coach.s14x.core

import munit.ScalaCheckSuite
import org.scalacheck.Gen
import org.scalacheck.Prop.forAll

final class NumericPropertiesSuite extends ScalaCheckSuite:
  private def valueOf[A](result: Either[StableError, A]): A =
    result match
      case Right(value) => value
      case Left(error)  => fail(s"예상하지 않은 stable error: ${error.code}")

  private val prices =
    Gen.listOfN(12, Gen.choose(1.0, 10000.0)).map(values => Vector.from(values))

  property("simple/log return은 positive scaling에 불변이다"):
    forAll(prices, Gen.choose(0.01, 100.0)) { (values, scale) =>
      val scaled = values.map(_ * scale)
      val simple = valueOf(ProductionMetrics.simpleReturns(values))
      val scaledSimple = valueOf(ProductionMetrics.simpleReturns(scaled))
      val logs = valueOf(ProductionMetrics.logReturns(values))
      val scaledLogs = valueOf(ProductionMetrics.logReturns(scaled))
      simple.zip(scaledSimple).forall((left, right) => math.abs(left - right) <= 1.0e-12) &&
      logs.zip(scaledLogs).forall((left, right) => math.abs(left - right) <= 1.0e-12)
    }

  property("realized variance는 scale squared, volatility는 absolute scale이다"):
    forAll(
      Gen.listOfN(16, Gen.choose(-10.0, 10.0)).map(values => Vector.from(values)),
      Gen.choose(-10.0, 10.0).suchThat(_ != 0.0)
    ) { (values, scale) =>
      val rv = valueOf(AdvancedRisk.realizedVariance(values))
      val scaledRv = valueOf(AdvancedRisk.realizedVariance(values.map(_ * scale)))
      val rvol = valueOf(AdvancedRisk.realizedVolatilityIntraday(values))
      val scaledRvol =
        valueOf(AdvancedRisk.realizedVolatilityIntraday(values.map(_ * scale)))
      math.abs(scaledRv - rv * scale * scale) <= 1.0e-8 &&
      math.abs(scaledRvol - rvol * math.abs(scale)) <= 1.0e-9
    }

  property("PSR은 observed와 benchmark가 같으면 0.5다"):
    forAll(Gen.choose(-4.0, 4.0), Gen.choose(2, 10000)) { (sharpe, count) =>
      valueOf(
        AdvancedRisk.probabilisticSharpeRatio(
          sharpe,
          sharpe,
          BigInt(count),
          0.0,
          3.0
        )
      ) == 0.5
    }

  property("max drawdown은 [-1,0]이고 positive scaling에 불변이다"):
    forAll(prices, Gen.choose(0.01, 100.0)) { (values, scale) =>
      val drawdown = valueOf(ProductionMetrics.maxDrawdown(values))
      val scaled = valueOf(ProductionMetrics.maxDrawdown(values.map(_ * scale)))
      drawdown >= -1.0 && drawdown <= 0.0 && math.abs(drawdown - scaled) <= 1.0e-12
    }
