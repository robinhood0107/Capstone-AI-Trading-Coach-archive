package s1_4x.source_policy_negative

type Lookup = Map[String, Int]

object TypeAliasMapApply:
  def lookup(values: Lookup, key: String): Int = values(key)
