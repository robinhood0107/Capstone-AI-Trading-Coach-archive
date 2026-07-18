package s1_4x.source_policy_negative

import java.lang.Integer.{parseInt as parse}

object RenamedIntegerParse:
  def decode(value: String): Int = parse(value)
