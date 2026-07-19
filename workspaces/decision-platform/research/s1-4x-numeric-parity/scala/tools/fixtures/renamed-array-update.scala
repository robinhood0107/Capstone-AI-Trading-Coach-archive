package s1_4x.source_policy_negative

import scala.{Array as Packed}

object RenamedArrayUpdate:
  def replaceFirst(values: Packed[Double]): Unit = values.update(0, 1.0)
