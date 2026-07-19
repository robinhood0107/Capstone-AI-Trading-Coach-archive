package s1_4x.source_policy_negative

import scala.Predef.{require as enforce}

object RenamedPredefRequire:
  def validate(condition: Boolean): Unit = enforce(condition)
