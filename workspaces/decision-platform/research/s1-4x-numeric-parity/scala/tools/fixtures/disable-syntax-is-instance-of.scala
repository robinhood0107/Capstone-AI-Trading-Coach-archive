package s1_4x.source_policy_negative

object DisableSyntaxIsInstanceOf:
  def isText(value: Any): Boolean = value.isInstanceOf[String]
