package ai.trading.coach.s14x.shell

import com.fasterxml.jackson.core.JsonFactory
import com.fasterxml.jackson.core.StreamReadFeature
import com.fasterxml.jackson.databind.DeserializationFeature
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ObjectNode
import scala.jdk.CollectionConverters.*
import scala.util.control.NonFatal

enum FunctionId(val wire: String) derives CanEqual:
  case SimpleReturns extends FunctionId("simple_returns")
  case LogReturns extends FunctionId("log_returns")
  case CumulativeReturn extends FunctionId("cumulative_return")
  case Cagr extends FunctionId("cagr")
  case RealizedVolatility extends FunctionId("realized_volatility")
  case AnnualizedVolatility extends FunctionId("annualized_volatility")
  case MaxDrawdown extends FunctionId("max_drawdown")
  case SharpeRatio extends FunctionId("sharpe_ratio")
  case SortinoRatio extends FunctionId("sortino_ratio")
  case HistoricalVar extends FunctionId("historical_var")
  case HistoricalCvar extends FunctionId("historical_cvar")
  case HistoricalExpectedShortfall extends FunctionId("historical_expected_shortfall")
  case RealizedVariance extends FunctionId("realized_variance")
  case RealizedVolatilityIntraday extends FunctionId("realized_volatility_intraday")
  case LoAdjustedSharpeRatio extends FunctionId("lo_adjusted_sharpe_ratio")
  case ProbabilisticSharpeRatio extends FunctionId("probabilistic_sharpe_ratio")
  case DeflatedSharpeRatio extends FunctionId("deflated_sharpe_ratio")
  case KupiecUnconditionalCoverageTest
      extends FunctionId("kupiec_unconditional_coverage_test")
  case ChristoffersenIndependenceTest
      extends FunctionId("christoffersen_independence_test")
  case ChristoffersenConditionalCoverageTest
      extends FunctionId("christoffersen_conditional_coverage_test")

object FunctionId:
  private val ByWire = values.map(value => value.wire -> value).toMap
  def fromWire(value: String): Option[FunctionId] = ByWire.get(value)

final case class TransportError(
    code: String,
    requestId: Option[String] = None,
    fixtureId: Option[String] = None,
    field: Option[String] = None,
) derives CanEqual

final case class CanonicalCase(
    fixtureId: String,
    functionId: FunctionId,
    arguments: ObjectNode,
) derives CanEqual

final case class CanonicalRequest(requestId: String, cases: Vector[CanonicalCase]) derives CanEqual

object ContractDecoder:
  private val Identifier = "^[a-z0-9](?:[a-z0-9._:-]{0,126}[a-z0-9])?$".r
  private val NegativeIntegerZero = "(?<![0-9.])-0(?=\\s*[,}\\]])".r
  private val Factory =
    JsonFactory
      .builder()
      .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
      .build()
  private val Mapper =
    ObjectMapper(Factory)
      .enable(DeserializationFeature.USE_BIG_INTEGER_FOR_INTS)
      .enable(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS)

  private val EnvelopeFields = Set("schemaVersion", "requestId", "cases")
  private val CaseFields =
    Set("fixtureId", "functionId", "arguments", "expectedSemanticError", "toleranceClass")

  private val ArgumentContract: Map[FunctionId, (Set[String], Set[String])] = Map(
    FunctionId.SimpleReturns -> (Set("prices"), Set.empty),
    FunctionId.LogReturns -> (Set("prices"), Set.empty),
    FunctionId.CumulativeReturn -> (Set("returns"), Set.empty),
    FunctionId.Cagr -> (Set("prices"), Set("periods_per_year")),
    FunctionId.RealizedVolatility -> (Set("log_returns"), Set.empty),
    FunctionId.AnnualizedVolatility -> (Set("log_returns"), Set("periods_per_year")),
    FunctionId.MaxDrawdown -> (Set("equity_curve"), Set.empty),
    FunctionId.SharpeRatio -> (Set("returns"), Set("risk_free_rate", "periods_per_year")),
    FunctionId.SortinoRatio -> (Set("returns"), Set("target_return", "periods_per_year")),
    FunctionId.HistoricalVar -> (Set("returns"), Set("confidence")),
    FunctionId.HistoricalCvar -> (Set("returns"), Set("confidence")),
    FunctionId.HistoricalExpectedShortfall -> (Set("losses"), Set("confidence")),
    FunctionId.RealizedVariance -> (Set("intraday_log_returns"), Set.empty),
    FunctionId.RealizedVolatilityIntraday -> (Set("intraday_log_returns"), Set.empty),
    FunctionId.LoAdjustedSharpeRatio ->
      (Set("returns", "aggregation_periods"), Set("risk_free_rate")),
    FunctionId.ProbabilisticSharpeRatio ->
      (
        Set("observed_sharpe", "benchmark_sharpe", "sample_size", "skewness", "kurtosis"),
        Set.empty,
      ),
    FunctionId.DeflatedSharpeRatio ->
      (
        Set(
          "observed_sharpe",
          "sample_size",
          "skewness",
          "kurtosis",
          "trial_count",
          "sharpe_estimate_variance",
          "trial_provenance",
        ),
        Set.empty,
      ),
    FunctionId.KupiecUnconditionalCoverageTest ->
      (Set("realized_losses", "forecast_vars", "confidence"), Set("significance")),
    FunctionId.ChristoffersenIndependenceTest ->
      (Set("realized_losses", "forecast_vars"), Set("significance")),
    FunctionId.ChristoffersenConditionalCoverageTest ->
      (Set("realized_losses", "forecast_vars", "confidence"), Set("significance")),
  )

  private def fields(node: JsonNode): Set[String] =
    node.fieldNames().asScala.toSet

  private def textValue(node: JsonNode, name: String): Option[String] =
    val value = node.path(name)
    if value.isTextual then Some(value.textValue()) else None

  private def decodeCase(node: JsonNode): Either[TransportError, CanonicalCase] =
    val fixture = textValue(node, "fixtureId")
    val function = textValue(node, "functionId").flatMap(FunctionId.fromWire)
    val arguments = node.path("arguments")
    val caseFieldsValid = node.isObject && fields(node).subsetOf(CaseFields)
    (fixture, function) match
      case (Some(fixtureId), Some(functionId))
          if caseFieldsValid &&
            Identifier.matches(fixtureId) &&
            arguments.isObject &&
            arguments.size() >= 1 &&
            arguments.size() <= 8 =>
        val objectArguments = arguments.deepCopy[ObjectNode]()
        val argumentFields = fields(objectArguments)
        ArgumentContract.get(functionId) match
          case Some((required, optional))
              if required.subsetOf(argumentFields) &&
                argumentFields.subsetOf(required ++ optional) =>
            Right(CanonicalCase(fixtureId, functionId, objectArguments))
          case _ => Left(TransportError("request_invalid", fixtureId = Some(fixtureId)))
      case _ => Left(TransportError("request_invalid", fixtureId = fixture))

  /** UTF-8 decode 후 호출되며 duplicate key와 integer -0을 tree materialization 전에 거부한다. */
  def decode(rawText: String): Either[TransportError, CanonicalRequest] =
    if NegativeIntegerZero.findFirstIn(rawText).nonEmpty then
      Left(TransportError("request_invalid"))
    else
      try
        val root = Mapper.readTree(rawText)
        if !root.isObject || fields(root) != EnvelopeFields then
          Left(TransportError("request_invalid"))
        else
          val schema = root.path("schemaVersion")
          val requestId = textValue(root, "requestId")
          val cases = root.path("cases")
          requestId match
            case Some(identifier)
                if schema.isTextual &&
                  schema.textValue() == "s1.4x-request-v1" &&
                  Identifier.matches(identifier) &&
                  cases.isArray &&
                  cases.size() >= 1 &&
                  cases.size() <= 4096 =>
              val decoded = cases.elements().asScala.toVector.map(decodeCase)
              decoded.collectFirst { case Left(error) => error } match
                case Some(error) => Left(error.copy(requestId = Some(identifier)))
                case None =>
                  val values = decoded.collect { case Right(value) => value }
                  if values.map(_.fixtureId).distinct.size != values.size then
                    Left(TransportError("request_invalid", requestId = Some(identifier)))
                  else Right(CanonicalRequest(identifier, values))
            case _ => Left(TransportError("request_invalid", requestId = requestId))
      catch case NonFatal(_) => Left(TransportError("request_invalid"))

  private[shell] def mapper: ObjectMapper = Mapper
