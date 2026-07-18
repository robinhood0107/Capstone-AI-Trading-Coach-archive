package ai.trading.coach.s14x.shell

import ai.trading.coach.s14x.core.ConditionalCoverageResult
import ai.trading.coach.s14x.core.IndependenceResult
import ai.trading.coach.s14x.core.LikelihoodResult
import ai.trading.coach.s14x.core.NumericResult
import ai.trading.coach.s14x.core.Transitions
import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.node.ArrayNode
import com.fasterxml.jackson.databind.node.ObjectNode

object JsonSupport:
  private val Mapper = ContractDecoder.mapper

  private def number(value: Double): JsonNode =
    Mapper.getNodeFactory.numberNode(if value == 0.0 then 0.0 else value)

  def normalizeNumberTree(value: Any): Any =
    value match
      case numberValue: Double => if numberValue == 0.0 then 0.0 else numberValue
      case vector: Vector[?]   => vector.map(normalizeNumberTree)
      case map: Map[?, ?] =>
        map.toVector.collect { case (key: String, item) => key -> normalizeNumberTree(item) }.toMap
      case other => other

  private def node(value: Any): JsonNode =
    value match
      case value: String  => Mapper.getNodeFactory.textNode(value)
      case value: Boolean => Mapper.getNodeFactory.booleanNode(value)
      case value: Int     => Mapper.getNodeFactory.numberNode(value)
      case value: Long    => Mapper.getNodeFactory.numberNode(value)
      case value: BigInt  => Mapper.getNodeFactory.numberNode(value.bigInteger)
      case value: Double  => number(value)
      case values: Vector[?] =>
        values.foldLeft(Mapper.createArrayNode())((array, item) => array.add(node(item)))
      case values: Map[?, ?] =>
        values.toVector.collect { case (key: String, item) => key -> item }.sortBy(_._1).foldLeft(
          Mapper.createObjectNode()
        ) { case (objectNode, (key, item)) =>
          val _ = objectNode.set[JsonNode](key, node(item))
          objectNode
        }
      case _ => Mapper.getNodeFactory.textNode("unsupported")

  def encode(value: Any): String =
    Mapper.writeValueAsString(node(normalizeNumberTree(value)))

  private def transitionsNode(value: Transitions): ObjectNode =
    val result = Mapper.createObjectNode()
    result.put("n00", value.n00)
    result.put("n01", value.n01)
    result.put("n10", value.n10)
    result.put("n11", value.n11)
    result

  private def likelihoodFields(result: ObjectNode, value: LikelihoodResult): ObjectNode =
    result.put("statistic", if value.statistic == 0.0 then 0.0 else value.statistic)
    result.put("p_value", if value.pValue == 0.0 then 0.0 else value.pValue)
    result.put("reject", value.reject)
    result.put("observations", value.observations)
    result.put("exceptions", value.exceptions)
    result.put("degrees_of_freedom", value.degreesOfFreedom)
    result.put("significance", value.significance)
    result

  private def resultValue(value: NumericResult): JsonNode =
    value match
      case NumericResult.Scalar(numberValue) => number(numberValue)
      case NumericResult.VectorResult(values) =>
        values.foldLeft(Mapper.createArrayNode())((array, item) => array.add(number(item)))
      case NumericResult.Likelihood(result) =>
        likelihoodFields(Mapper.createObjectNode(), result)
      case NumericResult.Independence(result: IndependenceResult) =>
        val base = likelihoodFields(
          Mapper.createObjectNode(),
          LikelihoodResult(
            result.statistic,
            result.pValue,
            result.reject,
            result.observations,
            result.exceptions,
            result.degreesOfFreedom,
            result.significance,
          ),
        )
        base.set[ObjectNode]("transitions", transitionsNode(result.transitions))
        base
      case NumericResult.Conditional(result: ConditionalCoverageResult) =>
        val base = likelihoodFields(
          Mapper.createObjectNode(),
          LikelihoodResult(
            result.statistic,
            result.pValue,
            result.reject,
            result.observations,
            result.exceptions,
            result.degreesOfFreedom,
            result.significance,
          ),
        )
        base.set[ObjectNode]("transitions", transitionsNode(result.transitions))
        base.put("conditioned_observations", result.conditionedObservations)
        base.put("conditioned_exceptions", result.conditionedExceptions)
        base.put(
          "unconditional_component_statistic",
          if result.unconditionalComponentStatistic == 0.0 then 0.0
          else result.unconditionalComponentStatistic,
        )
        base.put(
          "independence_component_statistic",
          if result.independenceComponentStatistic == 0.0 then 0.0
          else result.independenceComponentStatistic,
        )
        base

  def batchNode(batch: CandidateBatch): ObjectNode =
    val root = Mapper.createObjectNode()
    root.put("schemaVersion", "s1.4x-result-batch-v1")
    root.put("requestId", batch.requestId)
    root.put("implementation", "scala-3.8.4-jvm25")
    val results = batch.results.foldLeft(Mapper.createArrayNode()) { (array, result) =>
      val item = Mapper.createObjectNode()
      item.put("schemaVersion", "s1.4x-result-v1")
      item.put("functionId", result.functionId.wire)
      item.put("fixtureId", result.fixtureId)
      result.value match
        case Left(error) =>
          item.put("status", "error")
          item.put("errorCode", error.code)
        case Right(value) =>
          item.put("status", "ok")
          item.set[JsonNode]("values", resultValue(value))
      array.add(item)
    }
    root.set[ArrayNode]("results", results)
    root

  def transportNode(error: TransportError): ObjectNode =
    val root = Mapper.createObjectNode()
    root.put("schemaVersion", "s1.4x-transport-error-v1")
    root.put("code", error.code)
    error.requestId.foreach(value => root.put("requestId", value))
    error.fixtureId.foreach(value => root.put("fixtureId", value))
    error.field.foreach(value => root.put("field", value))
    root

  def bytes(node: JsonNode): Array[Byte] =
    (Mapper.writeValueAsString(node) + "\n").getBytes(java.nio.charset.StandardCharsets.UTF_8)
