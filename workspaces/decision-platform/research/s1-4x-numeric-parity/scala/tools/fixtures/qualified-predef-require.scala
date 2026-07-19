package s1_4x.source_policy_negative

object QualifiedPredefRequire:
  def validate(condition: Boolean): Unit = scala.Predef.require(condition)
