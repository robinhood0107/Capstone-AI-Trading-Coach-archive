package ai.trading.coach.s14x.core

import munit.FunSuite

final class AdvancedRiskSuite extends FunSuite:
  private val tolerance = 1.0e-11

  private def valueOf[A](result: Either[StableError, A]): A =
    result match
      case Right(value) => value
      case Left(error)  => fail(s"예상하지 않은 stable error: ${error.code}")

  private def errorOf[A](result: Either[StableError, A]): StableError =
    result match
      case Left(error) => error
      case Right(_)    => fail("stable error가 필요하다")

  private val provenance =
    TrialProvenance(
      schemaVersion = "s1.4r-effective-trials-v1",
      method = "pre_registered_independent",
      rawTrialCount = BigInt(2),
      effectiveTrialCount = BigInt(2),
      samplingFrequency = "daily",
      trialRegistrySha256 = "a" * 64,
      varianceDdof = BigInt(1)
    )

  test("9개 research function의 hand-paper 계약"):
    val losses = Vector(1.0, 2.0, 3.0, 4.0)
    assertEqualsDouble(
      valueOf(AdvancedRisk.historicalExpectedShortfall(losses, 0.625)),
      11.0 / 3.0,
      tolerance
    )
    assertEquals(valueOf(AdvancedRisk.historicalExpectedShortfall(losses, 0.5)), 3.5)
    assertEquals(valueOf(AdvancedRisk.historicalExpectedShortfall(losses, 0.9)), 4.0)
    assertEquals(
      valueOf(
        AdvancedRisk.historicalExpectedShortfall(Vector(1.0e308, 1.0e308, 0.0, 0.0), 0.5)
      ),
      1.0e308
    )
    val intraday = Vector(1.0, -2.0, 2.0)
    assertEquals(valueOf(AdvancedRisk.realizedVariance(intraday)), 9.0)
    assertEquals(valueOf(AdvancedRisk.realizedVolatilityIntraday(intraday)), 3.0)
    assertEqualsDouble(
      valueOf(AdvancedRisk.loAdjustedSharpeRatio(Vector(-1.0, 0.0, 1.0, 2.0), BigInt(2), 0.0)),
      0.565685424949238,
      tolerance
    )
    assertEqualsDouble(
      valueOf(AdvancedRisk.probabilisticSharpeRatio(1.0, 0.0, BigInt(6), 0.0, 3.0)),
      0.9660554225690855,
      tolerance
    )
    assertEquals(
      valueOf(AdvancedRisk.probabilisticSharpeRatio(1.0, 1.0, BigInt(6), 0.0, 3.0)),
      0.5
    )
    assertEqualsDouble(
      valueOf(
        AdvancedRisk.deflatedSharpeRatio(
          1.0,
          BigInt(6),
          0.0,
          3.0,
          BigInt(2),
          1.0,
          provenance
        )
      ),
      0.8097031129023626,
      tolerance
    )

  test("coverage와 independence/conditional likelihood record"):
    val zero = valueOf(
      AdvancedRisk.kupiecUnconditionalCoverageTest(
        Vector(0.0, 0.0, 0.0, 0.0),
        Vector(1.0, 1.0, 1.0, 1.0),
        0.75,
        0.05
      )
    )
    assertEquals(zero.exceptions, 0)
    assertEquals(zero.observations, 4)
    assertEqualsDouble(zero.statistic, 2.301456579614247, tolerance)
    assertEqualsDouble(zero.pValue, 0.12925273959404257, tolerance)
    assertEquals(zero.reject, false)

    val realized = Vector(0.0, 2.0, 0.0, 2.0, 0.0)
    val forecast = Vector.fill(5)(1.0)
    val independence =
      valueOf(AdvancedRisk.christoffersenIndependenceTest(realized, forecast, 0.05))
    assertEquals(independence.transitions, Transitions(0, 2, 2, 0))
    assertEqualsDouble(independence.statistic, 5.545177444479562, tolerance)
    val conditional =
      valueOf(
        AdvancedRisk.christoffersenConditionalCoverageTest(
          realized,
          forecast,
          0.6,
          0.05
        )
      )
    assertEqualsDouble(conditional.unconditionalComponentStatistic, 0.16328797808102014, tolerance)
    assertEqualsDouble(conditional.independenceComponentStatistic, 5.545177444479562, tolerance)
    assertEqualsDouble(conditional.statistic, 5.708465422560582, tolerance)

  test("research error precedence"):
    assertEquals(
      errorOf(AdvancedRisk.historicalExpectedShortfall(Vector.empty, 0.95)),
      StableError.ResearchInputTooShort
    )
    assertEquals(
      errorOf(AdvancedRisk.loAdjustedSharpeRatio(Vector(0.0, 1.0), BigInt(2), 0.0)),
      StableError.ResearchInputTooShort
    )
    assertEquals(
      errorOf(AdvancedRisk.probabilisticSharpeRatio(1.0, 0.0, BigInt(6), 2.0, 1.0)),
      StableError.MomentInvalid
    )
    val badProvenance = provenance.copy(rawTrialCount = BigInt(3), effectiveTrialCount = BigInt(3))
    assertEquals(
      errorOf(
        AdvancedRisk.deflatedSharpeRatio(
          1.0,
          BigInt(6),
          0.0,
          3.0,
          BigInt(2),
          1.0,
          badProvenance
        )
      ),
      StableError.TrialProvenanceInvalid
    )
    assertEquals(
      errorOf(
        AdvancedRisk.kupiecUnconditionalCoverageTest(
          Vector(0.0, 2.0),
          Vector(1.0),
          0.75,
          0.05
        )
      ),
      StableError.ForecastShapeInvalid
    )
    assertEquals(
      errorOf(
        AdvancedRisk.christoffersenIndependenceTest(
          Vector(0.0, 0.0, 0.0, 0.0),
          Vector.fill(4)(1.0),
          0.05
        )
      ),
      StableError.InsufficientSample
    )
