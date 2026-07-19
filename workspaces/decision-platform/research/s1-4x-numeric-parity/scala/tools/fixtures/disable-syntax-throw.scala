package s1_4x.source_policy_negative

object DisableSyntaxThrow:
  def fail: Nothing = throw new IllegalStateException("negative fixture")
