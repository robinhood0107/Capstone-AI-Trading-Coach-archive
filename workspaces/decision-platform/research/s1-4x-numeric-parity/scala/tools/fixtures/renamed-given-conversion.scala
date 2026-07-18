package s1_4x.source_policy_negative

import scala.{Conversion as Coercion}

given renamedConversion: Coercion[Int, String] with
  def apply(value: Int): String = value.toString
