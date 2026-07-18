package ai.trading.coach.s14x.core

object Validation:
  private val MaxProductionInputLength = 100000
  // IEEE-754 max finite의 exact integer 값이다. Decimal 문자열 반올림으로 상한을 만들지 않는다.
  private val MaxFloat64Integer = BigInt(2).pow(1024) - BigInt(2).pow(971)
  private val Sha256Pattern = "^[0-9a-f]{64}$".r
  private val ProvenanceMethods =
    Set("pre_registered_independent", "externally_estimated_effective_count")

  /** Typed core 경계에서 길이·finite precedence를 적용하고 immutable snapshot을 반환한다. */
  def productionSequence(
      values: Vector[Double],
      minimumLength: Int,
  ): Either[StableError, Vector[Double]] =
    if values.isEmpty then Left(StableError.InputEmpty)
    else if values.size > MaxProductionInputLength then Left(StableError.InputTooLong)
    else if values.size < minimumLength then Left(StableError.InputTooShort)
    else if values.exists(value => !value.isFinite) then Left(StableError.InputNonFinite)
    else Right(Vector.from(values))

  /** Transport-representable raw list의 shape → bool → type precedence를 보존한다. */
  def productionRawSequence(
      raw: Any,
      minimumLength: Int,
  ): Either[StableError, Vector[Double]] =
    raw match
      case _: Boolean => Left(StableError.InputBoolInvalid)
      case values: Vector[?] =>
        val nested = values.exists {
          case _: Iterable[?] => true
          case _: Array[?]    => true
          case _              => false
        }
        val boolean = values.exists {
          case _: Boolean => true
          case _          => false
        }
        val invalid = values.exists {
          case _: Byte | _: Short | _: Int | _: Long | _: BigInt | _: Double => false
          case _                                                             => true
        }
        if nested then Left(StableError.InputShapeInvalid)
        else if boolean then Left(StableError.InputBoolInvalid)
        else if invalid then Left(StableError.InputTypeInvalid)
        else
          val doubles = values.map {
            case value: Byte   => value.toDouble
            case value: Short  => value.toDouble
            case value: Int    => value.toDouble
            case value: Long   => value.toDouble
            case value: BigInt => value.toDouble
            case value: Double => value
            case _             => Double.NaN
          }
          productionSequence(doubles, minimumLength)
      case _ => Left(StableError.InputTypeInvalid)

  def researchSequence(
      values: Vector[Double],
      minimumLength: Int = 1,
  ): Either[StableError, Vector[Double]] =
    if values.exists(value => !value.isFinite) then Left(StableError.ResearchInputInvalid)
    else if values.size < minimumLength then Left(StableError.ResearchInputTooShort)
    else Right(Vector.from(values))

  def positivePeriods(value: BigInt): Either[StableError, BigInt] =
    if value <= 0 then Left(StableError.PeriodsPerYearInvalid) else Right(value)

  def productionConfidence(value: Double): Either[StableError, Double] =
    if !value.isFinite || value <= 0.0 || value >= 1.0 then Left(StableError.ConfidenceInvalid)
    else Right(value)

  def researchConfidence(value: Double): Either[StableError, Double] =
    if !value.isFinite || value <= 0.0 || value >= 1.0 then
      Left(StableError.ResearchInputInvalid)
    else Right(value)

  def significance(value: Double): Either[StableError, Double] =
    if !value.isFinite || value <= 0.0 || value >= 1.0 then
      Left(StableError.SignificanceInvalid)
    else Right(value)

  def sampleSize(value: BigInt): Either[StableError, BigInt] =
    if value <= 1 then Left(StableError.ResearchInputTooShort)
    else if value > MaxFloat64Integer || !value.toDouble.isFinite then
      Left(StableError.ResearchInputInvalid)
    else Right(value)

  def trialCount(value: BigInt): Either[StableError, BigInt] =
    if value < 2 || value > MaxFloat64Integer || !value.toDouble.isFinite then
      Left(StableError.TrialCountInvalid)
    else Right(value)

  def momentPair(skewness: Double, kurtosis: Double): Either[StableError, Unit] =
    val lower = skewness * skewness + 1.0
    val tolerance =
      64.0 * Math.ulp(1.0) * math.max(1.0, math.max(math.abs(kurtosis), math.abs(lower)))
    if !skewness.isFinite || !kurtosis.isFinite || !lower.isFinite || kurtosis + tolerance < lower
    then Left(StableError.MomentInvalid)
    else Right(())

  def provenance(value: TrialProvenance, trialCount: BigInt): Either[StableError, Unit] =
    val valid =
      value.schemaVersion == "s1.4r-effective-trials-v1" &&
        ProvenanceMethods.contains(value.method) &&
        value.rawTrialCount >= value.effectiveTrialCount &&
        value.effectiveTrialCount >= 2 &&
        value.effectiveTrialCount == trialCount &&
        value.samplingFrequency.trim.nonEmpty &&
        Sha256Pattern.matches(value.trialRegistrySha256) &&
        value.varianceDdof == 1
    if valid then Right(()) else Left(StableError.TrialProvenanceInvalid)

  def finiteProduction(value: Double): Either[StableError, Double] =
    if value.isFinite then Right(NumericPrimitives.normalizeZero(value))
    else Left(StableError.ResultNonFinite)

  def finiteResearch(value: Double): Either[StableError, Double] =
    if value.isFinite then Right(NumericPrimitives.normalizeZero(value))
    else Left(StableError.ResearchResultNonFinite)
