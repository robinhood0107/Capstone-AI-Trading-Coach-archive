package s1_4x.source_policy_negative

type MaybeCount = Option[Int]

object TypeAliasOptionGet:
  def count(value: MaybeCount): Int = value.get
