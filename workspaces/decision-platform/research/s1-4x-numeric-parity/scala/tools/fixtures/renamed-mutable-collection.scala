package s1_4x.source_policy_negative

import scala.collection.{mutable as changing}

object RenamedMutableCollection:
  def empty: changing.ArrayBuffer[Int] = changing.ArrayBuffer.empty[Int]
