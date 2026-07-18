package ai.trading.coach.s14x.benchmark

import ai.trading.coach.s14x.core.AdvancedRisk
import ai.trading.coach.s14x.core.ConditionalCoverageResult
import ai.trading.coach.s14x.core.IndependenceResult
import ai.trading.coach.s14x.core.LikelihoodResult
import ai.trading.coach.s14x.core.ProductionMetrics
import ai.trading.coach.s14x.core.StableError
import ai.trading.coach.s14x.core.TrialProvenance
import ai.trading.coach.s14x.shell.BinaryArrayReader
import com.fasterxml.jackson.core.JsonFactory
import com.fasterxml.jackson.core.StreamReadFeature
import com.fasterxml.jackson.databind.DeserializationFeature
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ObjectNode
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import scala.jdk.CollectionConverters.*
import scala.util.control.NonFatal

/**
 * 한 JMH process가 실행할 frozen case와 검증 완료 입력이다. plan/manifest/hash/decode는
 * companion setup에서 끝나고 `run`에는 numeric kernel과 결과 강제 평가만 남는다.
 */
final class BenchmarkInvocation private (
    prepared: Option[BenchmarkInvocation.PreparedCase]
):
  import BenchmarkInvocation.PreparedCase

  private def scalar(result: Either[StableError, Double]): Double =
    result.fold(_ => Double.NaN, identity)

  private def vector(result: Either[StableError, Vector[Double]]): Double =
    result.fold(_ => Double.NaN, _.foldLeft(0.0)(_ + _))

  private def likelihood(result: LikelihoodResult): Double =
    result.statistic +
      result.pValue +
      (if result.reject then 1.0 else 0.0) +
      result.observations.toDouble +
      result.exceptions.toDouble +
      result.degreesOfFreedom.toDouble +
      result.significance

  private def independence(result: IndependenceResult): Double =
    likelihood(
      LikelihoodResult(
        result.statistic,
        result.pValue,
        result.reject,
        result.observations,
        result.exceptions,
        result.degreesOfFreedom,
        result.significance,
      )
    ) +
      result.transitions.n00.toDouble +
      result.transitions.n01.toDouble +
      result.transitions.n10.toDouble +
      result.transitions.n11.toDouble

  private def conditional(result: ConditionalCoverageResult): Double =
    independence(
      IndependenceResult(
        result.statistic,
        result.pValue,
        result.reject,
        result.observations,
        result.exceptions,
        result.degreesOfFreedom,
        result.significance,
        result.transitions,
      )
    ) +
      result.conditionedObservations.toDouble +
      result.conditionedExceptions.toDouble +
      result.unconditionalComponentStatistic +
      result.independenceComponentStatistic

  private def runPrepared(value: PreparedCase): Double =
    val arguments = value.arguments
    value.functionId match
      case "simple_returns" => vector(ProductionMetrics.simpleReturns(value.prices))
      case "log_returns" => vector(ProductionMetrics.logReturns(value.prices))
      case "cumulative_return" =>
        scalar(ProductionMetrics.cumulativeReturn(value.returns))
      case "cagr" =>
        scalar(ProductionMetrics.cagr(value.prices, arguments.periodsPerYear))
      case "realized_volatility" =>
        scalar(ProductionMetrics.realizedVolatility(value.returns))
      case "annualized_volatility" =>
        scalar(
          ProductionMetrics.annualizedVolatility(
            value.returns,
            arguments.periodsPerYear,
          )
        )
      case "max_drawdown" => scalar(ProductionMetrics.maxDrawdown(value.prices))
      case "sharpe_ratio" =>
        scalar(
          ProductionMetrics.sharpeRatio(
            value.returns,
            arguments.riskFreeRate,
            arguments.periodsPerYear,
          )
        )
      case "sortino_ratio" =>
        scalar(
          ProductionMetrics.sortinoRatio(
            value.returns,
            arguments.targetReturn,
            arguments.periodsPerYear,
          )
        )
      case "historical_var" =>
        scalar(ProductionMetrics.historicalVar(value.returns, arguments.confidence))
      case "historical_cvar" =>
        scalar(ProductionMetrics.historicalCvar(value.returns, arguments.confidence))
      case "historical_expected_shortfall" =>
        scalar(
          AdvancedRisk.historicalExpectedShortfall(
            value.returns,
            arguments.confidence,
          )
        )
      case "realized_variance" =>
        scalar(AdvancedRisk.realizedVariance(value.returns))
      case "realized_volatility_intraday" =>
        scalar(AdvancedRisk.realizedVolatilityIntraday(value.returns))
      case "lo_adjusted_sharpe_ratio" =>
        scalar(
          AdvancedRisk.loAdjustedSharpeRatio(
            value.returns,
            arguments.aggregationPeriods,
            arguments.riskFreeRate,
          )
        )
      case "probabilistic_sharpe_ratio" =>
        value.observedSharpes.foldLeft(0.0) { (sum, observed) =>
          sum + scalar(
            AdvancedRisk.probabilisticSharpeRatio(
              observed,
              arguments.benchmarkSharpe,
              arguments.sampleSize,
              arguments.skewness,
              arguments.kurtosis,
            )
          )
        }
      case "deflated_sharpe_ratio" =>
        value.trialInputs.foldLeft(0.0) {
          case (sum, (observed, trialCount, provenance)) =>
            sum + scalar(
              AdvancedRisk.deflatedSharpeRatio(
                observed,
                arguments.sampleSize,
                arguments.skewness,
                arguments.kurtosis,
                trialCount,
                arguments.sharpeEstimateVariance,
                provenance,
              )
            )
        }
      case "kupiec_unconditional_coverage_test" =>
        value.coverage.foldLeft(0.0) { case (sum, (realized, forecast)) =>
          sum + AdvancedRisk
            .kupiecUnconditionalCoverageTest(
              realized,
              forecast,
              arguments.confidence,
              arguments.significance,
            )
            .fold(_ => Double.NaN, likelihood)
        }
      case "christoffersen_independence_test" =>
        value.coverage.foldLeft(0.0) { case (sum, (realized, forecast)) =>
          sum + AdvancedRisk
            .christoffersenIndependenceTest(
              realized,
              forecast,
              arguments.significance,
            )
            .fold(_ => Double.NaN, independence)
        }
      case "christoffersen_conditional_coverage_test" =>
        value.coverage.foldLeft(0.0) { case (sum, (realized, forecast)) =>
          sum + AdvancedRisk
            .christoffersenConditionalCoverageTest(
              realized,
              forecast,
              arguments.confidence,
              arguments.significance,
            )
            .fold(_ => Double.NaN, conditional)
        }
      case _ => Double.NaN

  /**
   * Setup에서 입력 검증과 한 번의 강제 평가가 모두 성공해야 fork가 timing으로 진입한다.
   * 실패 시 process exit 70으로 닫아 JMH가 유효한 숫자를 만들지 못하게 한다.
   */
  def requireValidSetup(): Unit =
    // JMH setup의 강제 평가도 timing 밖 실패 경계다. numeric kernel이 예외를 내면
    // 유효한 score를 만들지 않고 fork 자체를 exit 70으로 닫는다.
    val valid =
      try prepared.exists(value => runPrepared(value).isFinite)
      catch case NonFatal(_) => false
    if !valid then System.exit(70)

  /** JMH Blackhole이 반환값을 consume하므로 vector/record의 모든 계산도 timer 안에서 평가된다. */
  def run(): Double = prepared.fold(Double.NaN)(runPrepared)

object BenchmarkInvocation:
  private val FrozenPlanSha256 =
    "caf00112f58723e277293f59ccedb48bbd9ec82d096d3118ee3a9ed72658d1d1"
  private val FrozenCaseCount = 89
  private val DsrRegistrySha256 = "d" * 64
  private val Mapper =
    ObjectMapper(
      JsonFactory
        .builder()
        .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
        .build()
    )
      .enable(DeserializationFeature.USE_BIG_INTEGER_FOR_INTS)
      .enable(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS)

  private final case class FixtureSpec(
      manifestFile: String,
      manifestSha256: String,
      fixtureId: String,
      argumentName: String,
      maximumCount: Int,
  )

  private val Prices = FixtureSpec(
    "large-prices-n100000.manifest.json",
    "778abae4d621653b448a40b2b854cdf0f2e6fc63b7f439bdde96aaba9b83e7b5",
    "large-prices-n100000",
    "prices",
    100000,
  )
  private val Returns = FixtureSpec(
    "large-returns-n100000.manifest.json",
    "10000aaf12ae80ba5d813ebf3012753d142088df19742e90a52467ca2c93f99a",
    "large-returns-n100000",
    "returns",
    100000,
  )
  private val CoverageRealized = FixtureSpec(
    "large-coverage-realized-losses-n3200000.manifest.json",
    "68b5c6c8e2eb5f502e7297ffdf63b3b635cce9131e27e37dfd1fb578a5e784b8",
    "large-coverage-realized-losses-n3200000",
    "realized_losses",
    3200000,
  )
  private val CoverageForecast = FixtureSpec(
    "large-coverage-forecast-var-n3200000.manifest.json",
    "f4c2eeab713a948bfd645dcd43457c0a90c38340f4e66043a8a622f452797142",
    "large-coverage-forecast-var-n3200000",
    "forecast_vars",
    3200000,
  )

  private final case class TrialMix(
      evaluationCount: Int,
      trialCount: BigInt,
  )

  private final case class FunctionArguments(
      periodsPerYear: BigInt,
      riskFreeRate: Double,
      targetReturn: Double,
      confidence: Double,
      significance: Double,
      aggregationPeriods: BigInt,
      benchmarkSharpe: Double,
      sampleSize: BigInt,
      skewness: Double,
      kurtosis: Double,
      sharpeEstimateVariance: Double,
      trialCountMix: Vector[TrialMix],
      trialCountProvenance: String,
  )

  private final case class PlanCase(
      caseId: String,
      familyId: String,
      functionId: String,
      fixtureId: String,
      vectorLength: Int,
      batchSize: Int,
      logicalOperationsPerInvocation: Int,
      functionArguments: FunctionArguments,
  )

  private final case class PreparedCase(
      planCase: PlanCase,
      prices: Vector[Double],
      returns: Vector[Double],
      observedSharpes: Vector[Double],
      trialInputs: Vector[(Double, BigInt, TrialProvenance)],
      coverage: Vector[(Vector[Double], Vector[Double])],
  ):
    val functionId: String = planCase.functionId
    val arguments: FunctionArguments = planCase.functionArguments

  private def sha256(path: Path): String =
    MessageDigest
      .getInstance("SHA-256")
      .digest(Files.readAllBytes(path))
      .iterator
      .map(byte => f"${byte & 0xff}%02x")
      .mkString

  private def descriptor(manifestFile: String): ObjectNode =
    val node = Mapper.createObjectNode()
    node.put("kind", "binaryFloat64")
    node.put("manifestFile", manifestFile)
    node

  private def loadFixture(
      fixtureRoot: Path,
      fixture: FixtureSpec,
      requiredCount: Int,
  ): Option[Vector[Double]] =
    val realRoot = fixtureRoot.toRealPath()
    val manifestPath = realRoot.resolve("large").resolve(fixture.manifestFile).normalize()
    val manifestBound =
      requiredCount >= 1 &&
        requiredCount <= fixture.maximumCount &&
        Files.isRegularFile(manifestPath) &&
        !Files.isSymbolicLink(manifestPath) &&
        manifestPath.toRealPath().startsWith(realRoot) &&
        sha256(manifestPath) == fixture.manifestSha256
    if !manifestBound then None
    else
      BinaryArrayReader
        .read(
          descriptor(fixture.manifestFile),
          realRoot,
          fixture.fixtureId,
          fixture.argumentName,
        )
        .toOption
        .filter(decoded =>
          decoded.expectedSemanticError.isEmpty &&
            decoded.values.size == fixture.maximumCount
        )
        .map(_.values.take(requiredCount))

  private def text(node: JsonNode, name: String): Option[String] =
    val value = node.path(name)
    if value.isTextual then Option(value.textValue()) else None

  private def positiveInt(node: JsonNode, name: String): Option[Int] =
    val value = node.path(name)
    if value.isIntegralNumber &&
      value.canConvertToInt &&
      value.intValue() >= 1
    then Some(value.intValue())
    else None

  private val DefaultArguments = FunctionArguments(
    periodsPerYear = BigInt(252),
    riskFreeRate = 0.0,
    targetReturn = 0.0,
    confidence = 0.95,
    significance = 0.05,
    aggregationPeriods = BigInt(5),
    benchmarkSharpe = 0.0,
    sampleSize = BigInt(252),
    skewness = 0.0,
    kurtosis = 3.0,
    sharpeEstimateVariance = 1.0,
    trialCountMix = Vector.empty,
    trialCountProvenance = "",
  )

  private def fields(node: JsonNode): Set[String] =
    if node.isObject then node.fieldNames().asScala.toSet else Set.empty

  private def exactBigInt(
      node: JsonNode,
      name: String,
      expected: BigInt,
  ): Option[BigInt] =
    val value = node.path(name)
    if value.isIntegralNumber && BigInt(value.bigIntegerValue()) == expected then
      Some(BigInt(value.bigIntegerValue()))
    else None

  private def exactDouble(
      node: JsonNode,
      name: String,
      expected: Double,
  ): Option[Double] =
    val value = node.path(name)
    if !value.isNumber then None
    else
      val decoded = value.doubleValue()
      if decoded.isFinite &&
        java.lang.Double.doubleToRawLongBits(decoded) ==
          java.lang.Double.doubleToRawLongBits(expected)
      then Some(decoded)
      else None

  private def trialSegment(
      node: JsonNode,
      expectedEvaluations: Int,
      expectedTrialCount: BigInt,
  ): Option[TrialMix] =
    if fields(node) != Set("evaluation_count", "trial_count") then None
    else
      for
        evaluations <- positiveInt(node, "evaluation_count")
        trialCount <- exactBigInt(node, "trial_count", expectedTrialCount)
        if evaluations == expectedEvaluations
      yield TrialMix(evaluations, trialCount)

  private def exactTrialMix(node: JsonNode): Option[Vector[TrialMix]] =
    val values =
      if node.isArray then node.elements().asScala.toVector else Vector.empty
    values match
      case Vector(first, second, third) =>
        for
          firstValue <- trialSegment(first, 5462, BigInt(2))
          secondValue <- trialSegment(second, 5461, BigInt(10).pow(20))
          thirdValue <- trialSegment(third, 5461, BigInt(10).pow(308))
        yield Vector(firstValue, secondValue, thirdValue)
      case _ => None

  private def parseFunctionArguments(
      functionId: String,
      node: JsonNode,
  ): Option[FunctionArguments] =
    val actualFields = fields(node)
    functionId match
      case "simple_returns" |
          "log_returns" |
          "cumulative_return" |
          "realized_volatility" |
          "max_drawdown" |
          "realized_variance" |
          "realized_volatility_intraday"
          if actualFields.isEmpty =>
        Some(DefaultArguments)
      case "cagr" | "annualized_volatility"
          if actualFields == Set("periods_per_year") =>
        exactBigInt(node, "periods_per_year", BigInt(252))
          .map(value => DefaultArguments.copy(periodsPerYear = value))
      case "sharpe_ratio"
          if actualFields == Set("periods_per_year", "risk_free_rate") =>
        for
          periods <- exactBigInt(node, "periods_per_year", BigInt(252))
          riskFree <- exactDouble(node, "risk_free_rate", 0.0)
        yield DefaultArguments.copy(
          periodsPerYear = periods,
          riskFreeRate = riskFree,
        )
      case "sortino_ratio"
          if actualFields == Set("periods_per_year", "target_return") =>
        for
          periods <- exactBigInt(node, "periods_per_year", BigInt(252))
          target <- exactDouble(node, "target_return", 0.0)
        yield DefaultArguments.copy(
          periodsPerYear = periods,
          targetReturn = target,
        )
      case "historical_var" |
          "historical_cvar" |
          "historical_expected_shortfall"
          if actualFields == Set("confidence") =>
        exactDouble(node, "confidence", 0.95)
          .map(value => DefaultArguments.copy(confidence = value))
      case "lo_adjusted_sharpe_ratio"
          if actualFields == Set("aggregation_periods", "risk_free_rate") =>
        for
          periods <- exactBigInt(node, "aggregation_periods", BigInt(5))
          riskFree <- exactDouble(node, "risk_free_rate", 0.0)
        yield DefaultArguments.copy(
          aggregationPeriods = periods,
          riskFreeRate = riskFree,
        )
      case "probabilistic_sharpe_ratio"
          if actualFields ==
            Set("benchmark_sharpe", "kurtosis", "sample_size", "skewness") =>
        for
          benchmark <- exactDouble(node, "benchmark_sharpe", 0.0)
          kurtosis <- exactDouble(node, "kurtosis", 3.0)
          sampleSize <- exactBigInt(node, "sample_size", BigInt(252))
          skewness <- exactDouble(node, "skewness", 0.0)
        yield DefaultArguments.copy(
          benchmarkSharpe = benchmark,
          kurtosis = kurtosis,
          sampleSize = sampleSize,
          skewness = skewness,
        )
      case "deflated_sharpe_ratio"
          if actualFields == Set(
            "kurtosis",
            "sample_size",
            "sharpe_estimate_variance",
            "skewness",
            "trial_count_mix",
            "trial_count_provenance",
          ) =>
        for
          kurtosis <- exactDouble(node, "kurtosis", 3.0)
          sampleSize <- exactBigInt(node, "sample_size", BigInt(252))
          variance <- exactDouble(node, "sharpe_estimate_variance", 1.0)
          skewness <- exactDouble(node, "skewness", 0.0)
          mix <- exactTrialMix(node.path("trial_count_mix"))
          provenance <- text(node, "trial_count_provenance")
          if provenance == "externally_estimated_effective_count"
        yield DefaultArguments.copy(
          kurtosis = kurtosis,
          sampleSize = sampleSize,
          sharpeEstimateVariance = variance,
          skewness = skewness,
          trialCountMix = mix,
          trialCountProvenance = provenance,
        )
      case "kupiec_unconditional_coverage_test" |
          "christoffersen_conditional_coverage_test"
          if actualFields == Set("confidence", "significance") =>
        for
          confidence <- exactDouble(node, "confidence", 0.95)
          significance <- exactDouble(node, "significance", 0.05)
        yield DefaultArguments.copy(
          confidence = confidence,
          significance = significance,
        )
      case "christoffersen_independence_test"
          if actualFields == Set("significance") =>
        exactDouble(node, "significance", 0.05)
          .map(value => DefaultArguments.copy(significance = value))
      case _ => None

  private def selectPlanCase(
      planPath: Path,
      caseId: String,
      expectedFamily: String,
  ): Option[PlanCase] =
    val planBound =
      planPath.isAbsolute &&
        Files.isRegularFile(planPath) &&
        !Files.isSymbolicLink(planPath) &&
        sha256(planPath) == FrozenPlanSha256
    if !planBound then None
    else
      val root = Mapper.readTree(Files.readString(planPath))
      val cases = root.path("cases")
      val entries =
        if cases.isArray then cases.elements().asScala.toVector else Vector.empty
      val caseIds = entries.flatMap(node => text(node, "caseId"))
      val closureValid =
        root.path("schemaVersion").textValue() == "s1.4x-benchmark-plan-v1" &&
          root.path("planId").textValue() == "s1.4x-full-same-host-v1" &&
          entries.size == FrozenCaseCount &&
          caseIds.size == FrozenCaseCount &&
          caseIds.distinct.size == FrozenCaseCount
      val matching = entries.filter(node => text(node, "caseId").contains(caseId))
      if !closureValid || matching.size != 1 then None
      else
        matching.foldLeft(Option.empty[PlanCase]) { (_, node) =>
          for
            selectedCaseId <- text(node, "caseId")
            familyId <- text(node, "familyId")
            functionId <- text(node, "functionId")
            fixtureId <- text(node, "fixtureId")
            vectorLength <- positiveInt(node, "vectorLength")
            batchSize <- positiveInt(node, "batchSize")
            operations <- positiveInt(node, "logicalOperationsPerInvocation")
            functionArguments <- parseFunctionArguments(
              functionId,
              node.path("functionArguments"),
            )
            if selectedCaseId == caseId
            if familyId == expectedFamily
          yield PlanCase(
            selectedCaseId,
            familyId,
            functionId,
            fixtureId,
            vectorLength,
            batchSize,
            operations,
            functionArguments,
          )
        }

  private def trialInputs(
      observed: Vector[Double],
      trialMix: Vector[TrialMix],
      provenanceMethod: String,
  ): Vector[(Double, BigInt, TrialProvenance)] =
    val counts = trialMix.flatMap(segment =>
      Vector.fill(segment.evaluationCount)(segment.trialCount)
    )
    observed.zip(counts).map { case (observedSharpe, trialCount) =>
      (
        observedSharpe,
        trialCount,
        TrialProvenance(
          "s1.4r-effective-trials-v1",
          provenanceMethod,
          trialCount,
          trialCount,
          "daily",
          DsrRegistrySha256,
          BigInt(1),
        ),
      )
    }

  private def prepare(
      planCase: PlanCase,
      fixtureRoot: Path,
  ): Option[PreparedCase] =
    val emptyDoubles = Vector.empty[Double]
    val emptyTrials = Vector.empty[(Double, BigInt, TrialProvenance)]
    val emptyCoverage = Vector.empty[(Vector[Double], Vector[Double])]
    if planCase.fixtureId.startsWith("large-prices-n100000-prefix-n") then
      loadFixture(fixtureRoot, Prices, planCase.vectorLength).map(prices =>
        PreparedCase(
          planCase,
          prices,
          emptyDoubles,
          emptyDoubles,
          emptyTrials,
          emptyCoverage,
        )
      )
    else if planCase.fixtureId.startsWith("large-returns-n100000-prefix-n") then
      loadFixture(fixtureRoot, Returns, planCase.vectorLength).map(returns =>
        PreparedCase(
          planCase,
          emptyDoubles,
          returns,
          emptyDoubles,
          emptyTrials,
          emptyCoverage,
        )
      )
    else if planCase.fixtureId == "precomputed-probabilistic_sharpe_ratio-b16384" then
      loadFixture(fixtureRoot, Returns, 16384).map(observed =>
        PreparedCase(
          planCase,
          emptyDoubles,
          emptyDoubles,
          observed,
          emptyTrials,
          emptyCoverage,
        )
      )
    else if planCase.fixtureId == "precomputed-deflated_sharpe_ratio-b16384" then
      loadFixture(fixtureRoot, Returns, 16384).flatMap { observed =>
        val inputs =
          trialInputs(
            observed,
            planCase.functionArguments.trialCountMix,
            planCase.functionArguments.trialCountProvenance,
          )
        Option.when(inputs.size == observed.size)(
          PreparedCase(
            planCase,
            emptyDoubles,
            emptyDoubles,
            observed,
            inputs,
            emptyCoverage,
          )
        )
      }
    else if planCase.fixtureId.startsWith("large-coverage-pair-n3200000/prefix-n") then
      val requestedCount =
        BigInt(planCase.vectorLength) * BigInt(planCase.batchSize)
      if requestedCount >= 1 &&
        requestedCount <= CoverageRealized.maximumCount &&
        requestedCount.isValidInt
      then
        val count = requestedCount.toInt
        loadFixture(fixtureRoot, CoverageRealized, count)
          .zip(loadFixture(fixtureRoot, CoverageForecast, count))
          .map { case (realized, forecast) =>
            // 전체 곱이 maximumCount 이하임을 먼저 증명했으므로 아래 Int offset은 overflow하지 않는다.
            val sequences = Vector.tabulate(planCase.batchSize) { index =>
              val start = index * planCase.vectorLength
              (
                realized.slice(start, start + planCase.vectorLength),
                forecast.slice(start, start + planCase.vectorLength),
              )
            }
            PreparedCase(
              planCase,
              emptyDoubles,
              emptyDoubles,
              emptyDoubles,
              emptyTrials,
              sequences,
            )
          }
      else None
    else None

  /**
   * Public Gate 1 policy가 JMH `@Param`을 금지하므로 wrapper가 시작한 단일-case process
   * 환경을 읽는다. frozen plan SHA와 89-case closure, manifest SHA, payload hash/shape를 모두
   * 확인한 PreparedCase만 timing state로 전달한다.
   */
  def fromEnvironment(expectedFamily: String): BenchmarkInvocation =
    // Path/JSON/hash/binary decode는 외부 입력 경계다. 이 단일 NonFatal 경계에서
    // 어떤 malformed I/O도 None으로 닫아 JMH setup이 유효한 timing 결과를 만들지 못하게 한다.
    val prepared =
      try
        for
          caseId <- Option(System.getenv("S1_4X_BENCHMARK_CASE_ID"))
          planValue <- Option(System.getenv("S1_4X_BENCHMARK_PLAN"))
          fixtureValue <- Option(System.getenv("S1_4X_FIXTURE_ROOT"))
          planPath = Path.of(planValue).normalize()
          fixtureRoot = Path.of(fixtureValue).normalize()
          if planPath.isAbsolute
          if fixtureRoot.isAbsolute
          planCase <- selectPlanCase(planPath, caseId, expectedFamily)
          if planCase.logicalOperationsPerInvocation ==
            (if expectedFamily == "probabilistic-scalar" then 16384
             else if expectedFamily == "coverage-batch" then 32
             else 1)
          if planCase.batchSize ==
            (if expectedFamily == "probabilistic-scalar" then 16384
             else if expectedFamily == "coverage-batch" then 32
             else 1)
          value <- prepare(planCase, fixtureRoot)
        yield value
      catch case NonFatal(_) => None
    new BenchmarkInvocation(prepared)
