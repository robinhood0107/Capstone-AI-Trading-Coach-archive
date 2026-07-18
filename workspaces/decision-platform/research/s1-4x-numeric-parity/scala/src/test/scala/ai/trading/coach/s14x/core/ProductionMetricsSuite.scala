package ai.trading.coach.s14x.core

import munit.FunSuite

final class ProductionMetricsSuite extends FunSuite:
  private val tolerance = 1.0e-12

  private def valueOf[A](result: Either[StableError, A]): A =
    result match
      case Right(value) => value
      case Left(error)  => fail(s"예상하지 않은 stable error: ${error.code}")

  private def errorOf[A](result: Either[StableError, A]): StableError =
    result match
      case Left(error) => error
      case Right(_)    => fail("stable error가 필요하다")

  test("11개 production function의 hand-paper 계약"):
    assertEquals(
      valueOf(ProductionMetrics.simpleReturns(Vector(100.0, 200.0, 100.0))),
      Vector(1.0, -0.5)
    )
    assertEquals(
      valueOf(ProductionMetrics.logReturns(Vector(100.0, 100.0, 100.0))),
      Vector(0.0, 0.0)
    )
    assertEqualsDouble(
      valueOf(ProductionMetrics.cumulativeReturn(Vector(0.1, -0.1))),
      -0.009999999999999898,
      tolerance
    )
    assertEquals(
      valueOf(ProductionMetrics.cumulativeReturn(Vector(-1.0, 1.0e308))),
      -1.0
    )
    assertEqualsDouble(
      valueOf(ProductionMetrics.cagr(Vector(100.0, 110.0, 121.0), BigInt(2))),
      0.20999999999999977,
      tolerance
    )
    val logReturns = Vector(0.0, 0.1, -0.1)
    assertEqualsDouble(
      valueOf(ProductionMetrics.realizedVolatility(logReturns)),
      0.1,
      tolerance
    )
    assertEqualsDouble(
      valueOf(ProductionMetrics.annualizedVolatility(logReturns, BigInt(4))),
      0.2,
      tolerance
    )
    assertEquals(
      valueOf(ProductionMetrics.maxDrawdown(Vector(100.0, 120.0, 90.0, 108.0, 60.0))),
      -0.5
    )
    assertEquals(valueOf(ProductionMetrics.maxDrawdown(Vector(100.0, 0.0))), -1.0)
    val returns = Vector(-0.01, 0.02, 0.02)
    assertEqualsDouble(
      valueOf(ProductionMetrics.sharpeRatio(returns, 0.0, BigInt(1))),
      0.5773502691896257,
      tolerance
    )
    assertEqualsDouble(
      valueOf(ProductionMetrics.sortinoRatio(returns, 0.0, BigInt(1))),
      1.7320508075688772,
      tolerance
    )
    assertEqualsDouble(
      valueOf(ProductionMetrics.historicalVar(Vector(-0.1, -0.05, 0.0, 0.05, 0.1), 0.8)),
      -0.06000000000000001,
      tolerance
    )
    assertEquals(
      valueOf(ProductionMetrics.historicalCvar(Vector(-0.1, -0.05, -0.05, -0.05, 0.1), 0.6)),
      -0.0625
    )

  test("production stable error와 precedence"):
    assertEquals(
      errorOf(ProductionMetrics.simpleReturnsRaw(true)),
      StableError.InputBoolInvalid
    )
    assertEquals(
      errorOf(ProductionMetrics.simpleReturnsRaw(Vector(Vector(100.0), true))),
      StableError.InputShapeInvalid
    )
    assertEquals(
      errorOf(ProductionMetrics.cumulativeReturn(Vector.empty)),
      StableError.InputEmpty
    )
    assertEquals(
      errorOf(ProductionMetrics.realizedVolatility(Vector(0.0))),
      StableError.InputTooShort
    )
    assertEquals(
      errorOf(ProductionMetrics.logReturns(Vector(100.0, 0.0))),
      StableError.PricesNonPositive
    )
    assertEquals(
      errorOf(ProductionMetrics.cumulativeReturn(Vector(-1.0000000000000002))),
      StableError.SimpleReturnBelowMinusOne
    )
    assertEquals(
      errorOf(ProductionMetrics.sharpeRatio(Vector(0.01, 0.01), 0.0, BigInt(252))),
      StableError.DenominatorZero
    )
    assertEquals(
      errorOf(
        ProductionMetrics.historicalVar(
          Vector(-Double.MaxValue, Double.MaxValue),
          0.5
        )
      ),
      StableError.ResultNonFinite
    )

  test("core는 입력을 mutate하거나 alias하지 않고 -0을 정규화한다"):
    val prices = Vector(100.0, 100.0, 100.0)
    val before = prices
    val result = valueOf(ProductionMetrics.logReturns(prices))
    assertEquals(prices, before)
    assert(result.forall(value => java.lang.Double.doubleToRawLongBits(value) >= 0L))
    assertEquals(result, valueOf(ProductionMetrics.logReturns(prices)))
