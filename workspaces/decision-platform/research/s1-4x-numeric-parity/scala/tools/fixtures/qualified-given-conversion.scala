package s1_4x.source_policy_negative

given qualifiedConversion: scala.Conversion[Int, String] with
  def apply(value: Int): String = value.toString
