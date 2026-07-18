package ai.trading.coach.s14x

/** Gate 2 correctness가 입증되기 전에는 비최적화 Profile A만 authoritative 하다. */
object SelectedProfile:
  val profileId: String = "A"
  val additionalCompilerOptions: Vector[String] = Vector.empty
  val selectionStatus: String = "baseline-pending-qualification"
