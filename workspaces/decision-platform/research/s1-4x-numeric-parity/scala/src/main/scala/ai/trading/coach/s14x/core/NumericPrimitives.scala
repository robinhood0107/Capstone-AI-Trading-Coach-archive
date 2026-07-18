package ai.trading.coach.s14x.core

import org.apache.commons.numbers.gamma.Erfc

/**
 * 20개 numeric API가 공유하는 순차 Float64 primitive다.
 * 순서 변경·FMA·병렬 reduction 없이 frozen Python/JAX 기준의 branch와 안정 오류 계약을 보존한다.
 */
object NumericPrimitives:
  val EulerMascheroni: Double = 0.5772156649015329
  private val Float64Epsilon = Math.ulp(1.0)

  /** JSON과 cross-language comparator가 요구하는 positive zero 표현으로 경계값을 정규화한다. */
  def normalizeZero(value: Double): Double = if value == 0.0 then 0.0 else value

  /** 순서를 바꾸지 않는 compensated sum으로 큰 cancellation의 누적 오차를 줄인다. */
  def compensatedSum(values: IterableOnce[Double]): Double =
    values.iterator.foldLeft((0.0, 0.0)) { case ((sum, compensation), value) =>
      val candidate = sum + value
      val correction =
        if math.abs(sum) >= math.abs(value) then (sum - candidate) + value
        else (value - candidate) + sum
      (candidate, compensation + correction)
    } match
      case (sum, compensation) => sum + compensation

  /** 호출자가 길이·finite를 검증한 표본에 Bessel 보정을 적용하며 size 2 이상을 전제로 한다. */
  def sampleStandardDeviation(values: Vector[Double]): Double =
    val mean = compensatedSum(values) / values.size.toDouble
    val squared = values.map(value => (value - mean) * (value - mean))
    math.sqrt(compensatedSum(squared) / (values.size - 1).toDouble)

  /** 부동소수점 오차 범위의 확률만 [0, 1]로 clamp하고 그 밖의 값은 research 결과 오류로 닫는다. */
  def probability(value: Double): Either[StableError, Double] =
    val tolerance = 64.0 * Float64Epsilon
    if !value.isFinite || value < -tolerance || value > 1.0 + tolerance then
      Left(StableError.ResearchResultNonFinite)
    else Right(math.min(1.0, math.max(0.0, value)))

  /** Apache Commons Numbers Erfc를 사용해 표준정규 누적분포를 계산하며 입력 finite 검증은 호출자가 맡는다. */
  def normalCdf(value: Double): Double = 0.5 * Erfc.value(-value / math.sqrt(2.0))

  private def horner(argument: Double, coefficients: Vector[Double]): Double =
    coefficients.foldLeft(0.0)((accumulator, coefficient) =>
      accumulator * argument + coefficient
    )

  /** CPython 3.12 NormalDist가 사용하는 Wichura AS241의 branch와 coefficient를 보존한다. */
  def normalInverseCdf(probability: Double): Double =
    val q = probability - 0.5
    if math.abs(q) <= 0.425 then
      val r = 0.180625 - q * q
      val numerator =
        horner(
          r,
          Vector(
            2.5090809287301227e3,
            3.3430575583588128e4,
            6.72657709270087e4,
            4.592195393154987e4,
            1.373169376550946e4,
            1.9715909503065514e3,
            1.3314166789178438e2,
            3.3871328727963665,
          ),
        ) * q
      val denominator =
        horner(
          r,
          Vector(
            5.226495278852855e3,
            2.872908573572194e4,
            3.930789580009271e4,
            2.1213794301586597e4,
            5.394196021424751e3,
            6.871870074920579e2,
            4.231333070160091e1,
            1.0,
          ),
        )
      numerator / denominator
    else
      val tail = if q <= 0.0 then probability else 1.0 - probability
      val root = math.sqrt(-math.log(tail))
      val positive =
        if root <= 5.0 then
          val r = root - 1.6
          val numerator =
            horner(
              r,
              Vector(
                7.745450142783414e-4,
                2.2723844989269185e-2,
                2.417807251774506e-1,
                1.2704582524523684,
                3.6478483247632045,
                5.769497221460691,
                4.630337846156545,
                1.4234371107496835,
              ),
            )
          val denominator =
            horner(
              r,
              Vector(
                1.0507500716444169e-9,
                5.475938084995345e-4,
                1.5198666563616457e-2,
                1.4810397642748007e-1,
                6.897673349851e-1,
                1.6763848301838038,
                2.0531916266377588,
                1.0,
              ),
            )
          numerator / denominator
        else
          val r = root - 5.0
          val numerator =
            horner(
              r,
              Vector(
                2.010334399292288e-7,
                2.7115555687434876e-5,
                1.2426609473880784e-3,
                2.6532189526576123e-2,
                2.965605718285049e-1,
                1.7848265399172913,
                5.463784911164114,
                6.657904643501103,
              ),
            )
          val denominator =
            horner(
              r,
              Vector(
                2.0442631033899397e-15,
                1.4215117583164459e-7,
                1.8463183175100548e-5,
                7.868691311456133e-4,
                1.4875361290850615e-2,
                1.369298809227358e-1,
                5.99832206555888e-1,
                1.0,
              ),
            )
          numerator / denominator
      if q < 0.0 then -positive else positive

  /** 1 자유도 카이제곱 생존확률을 계산하고 probability 경계로 수치 오차를 검증한다. */
  def chiSquareOneSurvival(statistic: Double): Either[StableError, Double] =
    probability(Erfc.value(math.sqrt(statistic / 2.0)))

  /** 2 자유도 카이제곱 생존확률을 closed form으로 계산하고 확률 범위를 검증한다. */
  def chiSquareTwoSurvival(statistic: Double): Either[StableError, Double] =
    probability(math.exp(-statistic / 2.0))

  /** 두 log-likelihood 크기에 비례하는 Float64 허용오차를 반환해 작은 음수 통계만 zero로 정규화한다. */
  def likelihoodTolerance(nullLog: Double, alternativeLog: Double): Double =
    128.0 * Float64Epsilon *
      math.max(1.0, math.max(math.abs(nullLog), math.abs(alternativeLog)))

  /** finite null/alternative log-likelihood를 LR 통계로 변환하고 유의한 음수는 오류로 거부한다. */
  def likelihoodRatio(nullLog: Double, alternativeLog: Double): Either[StableError, Double] =
    if !nullLog.isFinite || !alternativeLog.isFinite then
      Left(StableError.ResearchResultNonFinite)
    else
      val statistic = 2.0 * (alternativeLog - nullLog)
      val tolerance = likelihoodTolerance(nullLog, alternativeLog)
      if statistic < -tolerance then Left(StableError.LikelihoodInvalid)
      else Validation.finiteResearch(if statistic < 0.0 then 0.0 else statistic)

  /** count가 0일 때 0*log(0)을 정확히 0으로 두고 나머지 확률 domain을 엄격히 검증한다. */
  def xlogProbability(count: Int, probability: Double): Either[StableError, Double] =
    if count == 0 then Right(0.0)
    else if probability <= 0.0 || probability > 1.0 then Left(StableError.LikelihoodInvalid)
    else Right(count.toDouble * math.log(probability))

  /** count가 0일 때 complement log 항을 0으로 두고, 그 외에는 log1p domain을 검증한다. */
  def xlogComplement(count: Int, probability: Double): Either[StableError, Double] =
    if count == 0 then Right(0.0)
    else if probability < 0.0 || probability >= 1.0 then Left(StableError.LikelihoodInvalid)
    else Right(count.toDouble * math.log1p(-probability))

  /** Bernoulli 관측/성공 횟수와 확률을 두 안전한 xlog 항으로 결합한다. */
  def bernoulliLogLikelihood(
      observations: Int,
      successes: Int,
      probability: Double,
  ): Either[StableError, Double] =
    for
      complement <- xlogComplement(observations - successes, probability)
      success <- xlogProbability(successes, probability)
    yield complement + success

  /** coverage confidence를 비예외 확률로 사용하는 frozen null log-likelihood를 반환한다. */
  def confidenceExceptionLogLikelihood(
      observations: Int,
      exceptions: Int,
      confidence: Double,
  ): Double =
    val nonExceptions = observations - exceptions
    val first = if nonExceptions == 0 then 0.0 else nonExceptions.toDouble * math.log(confidence)
    val second = if exceptions == 0 then 0.0 else exceptions.toDouble * math.log1p(-confidence)
    first + second
