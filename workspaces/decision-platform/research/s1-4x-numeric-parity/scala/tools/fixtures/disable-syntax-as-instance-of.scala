package s1_4x.source_policy_negative

object DisableSyntaxAsInstanceOf:
  def text(value: Any): String = value.asInstanceOf[String]
