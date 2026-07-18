package ai.trading.coach.s14x.core

/**
 * production과 research API의 입력·오류 precedence를 한 경계에서 고정한다.
 * 성공 값은 immutable 입력과 positive-zero 결과만 다음 numeric 단계로 전달한다.
 */
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

  /** research 배열은 finite를 먼저 확인한 뒤 최소 길이를 확인해 frozen 오류 precedence를 보존한다. */
  def researchSequence(
      values: Vector[Double],
      minimumLength: Int = 1,
  ): Either[StableError, Vector[Double]] =
    if values.exists(value => !value.isFinite) then Left(StableError.ResearchInputInvalid)
    else if values.size < minimumLength then Left(StableError.ResearchInputTooShort)
    else Right(Vector.from(values))

  /** 연환산 period는 임의 정밀도 정수로 받아 양수 여부만 먼저 검증한다. */
  def positivePeriods(value: BigInt): Either[StableError, BigInt] =
    if value <= 0 then Left(StableError.PeriodsPerYearInvalid) else Right(value)

  /** production confidence를 열린 구간 (0, 1)로 제한하고 production stable code를 반환한다. */
  def productionConfidence(value: Double): Either[StableError, Double] =
    if !value.isFinite || value <= 0.0 || value >= 1.0 then Left(StableError.ConfidenceInvalid)
    else Right(value)

  /** research confidence를 열린 구간 (0, 1)로 제한하고 research 입력 오류로 통일한다. */
  def researchConfidence(value: Double): Either[StableError, Double] =
    if !value.isFinite || value <= 0.0 || value >= 1.0 then
      Left(StableError.ResearchInputInvalid)
    else Right(value)

  /** hypothesis-test significance를 finite 열린 구간 (0, 1)로 제한한다. */
  def significance(value: Double): Either[StableError, Double] =
    if !value.isFinite || value <= 0.0 || value >= 1.0 then
      Left(StableError.SignificanceInvalid)
    else Right(value)

  /** PSR 표본 수는 2 이상이면서 Float64로 유한하게 표현 가능한 정수만 허용한다. */
  def sampleSize(value: BigInt): Either[StableError, BigInt] =
    if value <= 1 then Left(StableError.ResearchInputTooShort)
    else if value > MaxFloat64Integer || !value.toDouble.isFinite then
      Left(StableError.ResearchInputInvalid)
    else Right(value)

  /** DSR 시행 수는 2 이상이면서 Float64 변환이 유한한 정수만 허용한다. */
  def trialCount(value: BigInt): Either[StableError, BigInt] =
    if value < 2 || value > MaxFloat64Integer || !value.toDouble.isFinite then
      Left(StableError.TrialCountInvalid)
    else Right(value)

  /** skewness/kurtosis의 Pearson 하한을 scale-aware ulp 허용오차와 함께 검증한다. */
  def momentPair(skewness: Double, kurtosis: Double): Either[StableError, Unit] =
    val lower = skewness * skewness + 1.0
    val tolerance =
      64.0 * Math.ulp(1.0) * math.max(1.0, math.max(math.abs(kurtosis), math.abs(lower)))
    if !skewness.isFinite || !kurtosis.isFinite || !lower.isFinite || kurtosis + tolerance < lower
    then Left(StableError.MomentInvalid)
    else Right(())

  /** DSR trial provenance의 schema·count·registry hash·ddof를 frozen 계약과 대조한다. */
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

  /** production 결과의 finite 여부를 확인하고 성공한 zero의 부호를 정규화한다. */
  def finiteProduction(value: Double): Either[StableError, Double] =
    if value.isFinite then Right(NumericPrimitives.normalizeZero(value))
    else Left(StableError.ResultNonFinite)

  /** research 결과의 finite 여부를 확인하고 성공한 zero의 부호를 정규화한다. */
  def finiteResearch(value: Double): Either[StableError, Double] =
    if value.isFinite then Right(NumericPrimitives.normalizeZero(value))
    else Left(StableError.ResearchResultNonFinite)
