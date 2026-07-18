package ai.trading.coach.s14x.shell

import ai.trading.coach.s14x.core.AdvancedRisk
import ai.trading.coach.s14x.core.NumericResult
import ai.trading.coach.s14x.core.ProductionMetrics
import ai.trading.coach.s14x.core.StableError
import ai.trading.coach.s14x.core.TrialProvenance
import com.fasterxml.jackson.databind.JsonNode
import java.nio.file.Path
import scala.jdk.CollectionConverters.*

final case class CandidateCaseResult(
    fixtureId: String,
    functionId: FunctionId,
    value: Either[StableError, NumericResult],
) derives CanEqual:
  /** semantic result의 stable error code만 노출하며 transport error와 혼합하지 않는다. */
  def errorCode: Option[String] = value.left.toOption.map(_.code)

final case class CandidateBatch(requestId: String, results: Vector[CandidateCaseResult])
    derives CanEqual

object CandidateRunner:
  private def argument(testCase: CanonicalCase, name: String): JsonNode =
    testCase.arguments.path(name)

  private def productionArray(
      testCase: CanonicalCase,
      name: String,
      fixtureRoot: Path,
  ): Either[TransportError, Either[StableError, Vector[Double]]] =
    array(testCase, name, fixtureRoot, production = true)

  private def researchArray(
      testCase: CanonicalCase,
      name: String,
      fixtureRoot: Path,
  ): Either[TransportError, Either[StableError, Vector[Double]]] =
    array(testCase, name, fixtureRoot, production = false)

  private def array(
      testCase: CanonicalCase,
      name: String,
      fixtureRoot: Path,
      production: Boolean,
  ): Either[TransportError, Either[StableError, Vector[Double]]] =
    val node = argument(testCase, name)
    if node.isObject && node.path("kind").textValue() == "binaryFloat64" then
      BinaryArrayReader
        .read(node, fixtureRoot, testCase.fixtureId, name)
        .flatMap { decoded =>
          decoded.expectedSemanticError match
            case Some("input_non_finite") if production =>
              Right(Left(StableError.InputNonFinite))
            case Some("research_input_invalid") if !production =>
              Right(Left(StableError.ResearchInputInvalid))
            case Some(_) =>
              Left(
                TransportError(
                  "manifest_invalid",
                  fixtureId = Some(testCase.fixtureId),
                  field = Some("expectedSemanticError"),
                )
              )
            case None => Right(Right(decoded.values))
        }
    else if !node.isArray then
      val error =
        if production && node.isBoolean then StableError.InputBoolInvalid
        else if production then StableError.InputTypeInvalid
        else StableError.ResearchInputInvalid
      Right(Left(error))
    else
      val nodes = node.elements().asScala.toVector
      val nested = nodes.exists(value => value.isArray || value.isObject)
      val boolean = nodes.exists(_.isBoolean)
      val invalid = nodes.exists(value => !value.isNumber)
      if nested then
        Right(
          Left(
            if production then StableError.InputShapeInvalid else StableError.ResearchInputInvalid
          )
        )
      else if boolean then
        Right(
          Left(
            if production then StableError.InputBoolInvalid else StableError.ResearchInputInvalid
          )
        )
      else if invalid then
        Right(
          Left(
            if production then StableError.InputTypeInvalid else StableError.ResearchInputInvalid
          )
        )
      else
        val values = nodes.map(_.doubleValue())
        if values.exists(value => !value.isFinite) then
          Right(
            Left(
              if production then StableError.InputNonFinite
              else StableError.ResearchInputInvalid
            )
          )
        else Right(Right(values))

  private def real(
      testCase: CanonicalCase,
      name: String,
      default: Double,
      error: StableError,
  ): Either[StableError, Double] =
    val node = argument(testCase, name)
    if node.isMissingNode then Right(default)
    else if !node.isNumber then Left(error)
    else
      val value = node.doubleValue()
      if value.isFinite then Right(value) else Left(error)

  private def integer(
      testCase: CanonicalCase,
      name: String,
      default: Option[BigInt],
      error: StableError,
  ): Either[StableError, BigInt] =
    val node = argument(testCase, name)
    if node.isMissingNode then default.toRight(error)
    else if !node.isIntegralNumber then Left(error)
    else Right(BigInt(node.bigIntegerValue()))

  private def provenance(testCase: CanonicalCase): Either[StableError, TrialProvenance] =
    val node = argument(testCase, "trial_provenance")
    val fields =
      if node.isObject then node.fieldNames().asScala.toSet else Set.empty[String]
    val expected = Set(
      "schema_version",
      "method",
      "raw_trial_count",
      "effective_trial_count",
      "sampling_frequency",
      "trial_registry_sha256",
      "variance_ddof",
    )
    if fields != expected then Left(StableError.TrialProvenanceInvalid)
    else
      val integers =
        Vector("raw_trial_count", "effective_trial_count", "variance_ddof").forall(name =>
          node.path(name).isIntegralNumber
        )
      val strings =
        Vector("schema_version", "method", "sampling_frequency", "trial_registry_sha256").forall(
          name => node.path(name).isTextual
        )
      if !integers || !strings then Left(StableError.TrialProvenanceInvalid)
      else
        Right(
          TrialProvenance(
            node.path("schema_version").textValue(),
            node.path("method").textValue(),
            BigInt(node.path("raw_trial_count").bigIntegerValue()),
            BigInt(node.path("effective_trial_count").bigIntegerValue()),
            node.path("sampling_frequency").textValue(),
            node.path("trial_registry_sha256").textValue(),
            BigInt(node.path("variance_ddof").bigIntegerValue()),
          )
        )

  private def vectorResult(
      result: Either[StableError, Vector[Double]]
  ): Either[StableError, NumericResult] =
    result.map(NumericResult.VectorResult.apply)

  private def scalarResult(
      result: Either[StableError, Double]
  ): Either[StableError, NumericResult] =
    result.map(NumericResult.Scalar.apply)

  private def runCase(
      testCase: CanonicalCase,
      fixtureRoot: Path,
  ): Either[TransportError, CandidateCaseResult] =
    val computed: Either[TransportError, Either[StableError, NumericResult]] =
      testCase.functionId match
        case FunctionId.SimpleReturns =>
          productionArray(testCase, "prices", fixtureRoot)
            .map(_.flatMap(values => vectorResult(ProductionMetrics.simpleReturns(values))))
        case FunctionId.LogReturns =>
          productionArray(testCase, "prices", fixtureRoot)
            .map(_.flatMap(values => vectorResult(ProductionMetrics.logReturns(values))))
        case FunctionId.CumulativeReturn =>
          productionArray(testCase, "returns", fixtureRoot)
            .map(_.flatMap(values => scalarResult(ProductionMetrics.cumulativeReturn(values))))
        case FunctionId.Cagr =>
          productionArray(testCase, "prices", fixtureRoot).map { decoded =>
            for
              values <- decoded
              periods <- integer(
                testCase,
                "periods_per_year",
                Some(BigInt(252)),
                StableError.PeriodsPerYearInvalid,
              )
              result <- scalarResult(ProductionMetrics.cagr(values, periods))
            yield result
          }
        case FunctionId.RealizedVolatility =>
          productionArray(testCase, "log_returns", fixtureRoot).map(
            _.flatMap(values => scalarResult(ProductionMetrics.realizedVolatility(values)))
          )
        case FunctionId.AnnualizedVolatility =>
          productionArray(testCase, "log_returns", fixtureRoot).map { decoded =>
            for
              values <- decoded
              periods <- integer(
                testCase,
                "periods_per_year",
                Some(BigInt(252)),
                StableError.PeriodsPerYearInvalid,
              )
              result <- scalarResult(ProductionMetrics.annualizedVolatility(values, periods))
            yield result
          }
        case FunctionId.MaxDrawdown =>
          productionArray(testCase, "equity_curve", fixtureRoot).map(
            _.flatMap(values => scalarResult(ProductionMetrics.maxDrawdown(values)))
          )
        case FunctionId.SharpeRatio =>
          productionArray(testCase, "returns", fixtureRoot).map { decoded =>
            for
              values <- decoded
              riskFree <- real(
                testCase,
                "risk_free_rate",
                0.0,
                StableError.RiskFreeRateInvalid,
              )
              periods <- integer(
                testCase,
                "periods_per_year",
                Some(BigInt(252)),
                StableError.PeriodsPerYearInvalid,
              )
              result <- scalarResult(ProductionMetrics.sharpeRatio(values, riskFree, periods))
            yield result
          }
        case FunctionId.SortinoRatio =>
          productionArray(testCase, "returns", fixtureRoot).map { decoded =>
            for
              values <- decoded
              target <- real(testCase, "target_return", 0.0, StableError.TargetReturnInvalid)
              periods <- integer(
                testCase,
                "periods_per_year",
                Some(BigInt(252)),
                StableError.PeriodsPerYearInvalid,
              )
              result <- scalarResult(ProductionMetrics.sortinoRatio(values, target, periods))
            yield result
          }
        case FunctionId.HistoricalVar =>
          productionArray(testCase, "returns", fixtureRoot).map { decoded =>
            for
              values <- decoded
              confidence <- real(testCase, "confidence", 0.95, StableError.ConfidenceInvalid)
              result <- scalarResult(ProductionMetrics.historicalVar(values, confidence))
            yield result
          }
        case FunctionId.HistoricalCvar =>
          productionArray(testCase, "returns", fixtureRoot).map { decoded =>
            for
              values <- decoded
              confidence <- real(testCase, "confidence", 0.95, StableError.ConfidenceInvalid)
              result <- scalarResult(ProductionMetrics.historicalCvar(values, confidence))
            yield result
          }
        case FunctionId.HistoricalExpectedShortfall =>
          researchArray(testCase, "losses", fixtureRoot).map { decoded =>
            for
              values <- decoded
              confidence <- real(
                testCase,
                "confidence",
                0.95,
                StableError.ResearchInputInvalid,
              )
              result <- scalarResult(
                AdvancedRisk.historicalExpectedShortfall(values, confidence)
              )
            yield result
          }
        case FunctionId.RealizedVariance =>
          researchArray(testCase, "intraday_log_returns", fixtureRoot).map(
            _.flatMap(values => scalarResult(AdvancedRisk.realizedVariance(values)))
          )
        case FunctionId.RealizedVolatilityIntraday =>
          researchArray(testCase, "intraday_log_returns", fixtureRoot).map(
            _.flatMap(values =>
              scalarResult(AdvancedRisk.realizedVolatilityIntraday(values))
            )
          )
        case FunctionId.LoAdjustedSharpeRatio =>
          researchArray(testCase, "returns", fixtureRoot).map { decoded =>
            for
              values <- decoded
              periods <- integer(
                testCase,
                "aggregation_periods",
                None,
                StableError.AggregationPeriodsInvalid,
              )
              riskFree <- real(
                testCase,
                "risk_free_rate",
                0.0,
                StableError.ResearchInputInvalid,
              )
              result <- scalarResult(
                AdvancedRisk.loAdjustedSharpeRatio(values, periods, riskFree)
              )
            yield result
          }
        case FunctionId.ProbabilisticSharpeRatio =>
          Right(
            for
              observed <- real(
                testCase,
                "observed_sharpe",
                0.0,
                StableError.ResearchInputInvalid,
              )
              benchmark <- real(
                testCase,
                "benchmark_sharpe",
                0.0,
                StableError.ResearchInputInvalid,
              )
              sample <- integer(testCase, "sample_size", None, StableError.ResearchInputInvalid)
              skew <- real(testCase, "skewness", 0.0, StableError.MomentInvalid)
              kurtosis <- real(testCase, "kurtosis", 0.0, StableError.MomentInvalid)
              result <- scalarResult(
                AdvancedRisk.probabilisticSharpeRatio(
                  observed,
                  benchmark,
                  sample,
                  skew,
                  kurtosis,
                )
              )
            yield result
          )
        case FunctionId.DeflatedSharpeRatio =>
          Right(
            for
              observed <- real(
                testCase,
                "observed_sharpe",
                0.0,
                StableError.ResearchInputInvalid,
              )
              sample <- integer(testCase, "sample_size", None, StableError.ResearchInputInvalid)
              skew <- real(testCase, "skewness", 0.0, StableError.MomentInvalid)
              kurtosis <- real(testCase, "kurtosis", 0.0, StableError.MomentInvalid)
              trials <- integer(
                testCase,
                "trial_count",
                None,
                StableError.TrialCountInvalid,
              )
              variance <- real(
                testCase,
                "sharpe_estimate_variance",
                0.0,
                StableError.TrialVarianceInvalid,
              )
              trialProvenance <- provenance(testCase)
              result <- scalarResult(
                AdvancedRisk.deflatedSharpeRatio(
                  observed,
                  sample,
                  skew,
                  kurtosis,
                  trials,
                  variance,
                  trialProvenance,
                )
              )
            yield result
          )
        case FunctionId.KupiecUnconditionalCoverageTest =>
          for
            realized <- researchArray(testCase, "realized_losses", fixtureRoot)
            forecast <- researchArray(testCase, "forecast_vars", fixtureRoot)
          yield for
            realizedValues <- realized
            forecastValues <- forecast
            confidence <- real(
              testCase,
              "confidence",
              0.0,
              StableError.ResearchInputInvalid,
            )
            alpha <- real(testCase, "significance", 0.05, StableError.SignificanceInvalid)
            result <- AdvancedRisk
              .kupiecUnconditionalCoverageTest(
                realizedValues,
                forecastValues,
                confidence,
                alpha,
              )
              .map(NumericResult.Likelihood.apply)
          yield result
        case FunctionId.ChristoffersenIndependenceTest =>
          for
            realized <- researchArray(testCase, "realized_losses", fixtureRoot)
            forecast <- researchArray(testCase, "forecast_vars", fixtureRoot)
          yield for
            realizedValues <- realized
            forecastValues <- forecast
            alpha <- real(testCase, "significance", 0.05, StableError.SignificanceInvalid)
            result <- AdvancedRisk
              .christoffersenIndependenceTest(realizedValues, forecastValues, alpha)
              .map(NumericResult.Independence.apply)
          yield result
        case FunctionId.ChristoffersenConditionalCoverageTest =>
          for
            realized <- researchArray(testCase, "realized_losses", fixtureRoot)
            forecast <- researchArray(testCase, "forecast_vars", fixtureRoot)
          yield for
            realizedValues <- realized
            forecastValues <- forecast
            confidence <- real(
              testCase,
              "confidence",
              0.0,
              StableError.ResearchInputInvalid,
            )
            alpha <- real(testCase, "significance", 0.05, StableError.SignificanceInvalid)
            result <- AdvancedRisk
              .christoffersenConditionalCoverageTest(
                realizedValues,
                forecastValues,
                confidence,
                alpha,
              )
              .map(NumericResult.Conditional.apply)
          yield result
    computed.map(value => CandidateCaseResult(testCase.fixtureId, testCase.functionId, value))

  /** request order를 보존하고 transport failure 하나라도 있으면 partial batch를 만들지 않는다. */
  def execute(
      request: CanonicalRequest,
      fixtureRoot: Path,
  ): Either[TransportError, CandidateBatch] =
    val results = request.cases.map(testCase => runCase(testCase, fixtureRoot))
    results.collectFirst { case Left(error) => error } match
      case Some(error) =>
        Left(error.copy(requestId = Some(request.requestId)))
      case None =>
        Right(CandidateBatch(request.requestId, results.collect { case Right(value) => value }))
