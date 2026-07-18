package ai.trading.coach.s14x

/** A/B/C options와 proven fallback만 source에 고정한다. 실제 선택 ID는 frozen qualification을 재계산한 typed result가
  * 소유하므로 full benchmark를 보고 source를 바꾸는 manual override가 불가능하다.
  */
object SelectedProfile:
  final case class Definition(
      profileId: String,
      additionalCompilerOptions: Vector[String]
  )

  val definitions: Vector[Definition] = Vector(
    Definition("A", Vector.empty),
    Definition("B", Vector("-opt")),
    Definition(
      "C",
      Vector("-opt", "-opt-inline:ai.trading.coach.s14x.**")
    )
  )
  val fallbackProfileId: String = "A"
  val resultSchemaVersion: String = "s1.4x-scala-selected-profile-result-v1"
