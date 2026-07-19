package s1_4x.source_policy_negative

import java.lang.Math.{fma as fused}

object RenamedMathFma:
  def multiplyAdd(left: Double, right: Double, addend: Double): Double =
    fused(left, right, addend)
