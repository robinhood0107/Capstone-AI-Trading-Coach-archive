//> using scala 3.8.4
//> using jvm system
//> using option -source:3.8
//> using option -release:25
//> using option -encoding
//> using option UTF-8
//> using option -deprecation
//> using option -feature
//> using option -unchecked
//> using option -Wunused:all
//> using option -Wvalue-discard
//> using option -Wnonunit-statement
//> using option -Wenum-comment-discard
//> using option -Wimplausible-patterns
//> using option -WunstableInlineAccessors
//> using option -Wtostring-interpolated
//> using option -Wrecurse-with-default
//> using option -Wwrong-arrow
//> using option -Winfer-union
//> using option -Wshadow:all
//> using option -language:strictEquality
//> using option -language:noAutoTupling
//> using option -Werror
//> using dep com.fasterxml.jackson.core:jackson-core:2.22.1
//> using dep com.fasterxml.jackson.core:jackson-databind:2.22.1
//> using dep org.apache.commons:commons-numbers-gamma:1.3
//> using dep org.openjdk.jmh:jmh-core:1.37
//> using dep org.openjdk.jmh:jmh-generator-annprocess:1.37
//> using test.dep org.scalameta::munit:1.3.0
//> using test.dep org.scalameta::munit-scalacheck:1.3.0
//> using test.dep org.scalacheck::scalacheck:1.19.0

package ai.trading.coach.s14x

/** S1.4X의 exact Scala/JVM dependency와 compiler profile을 한 입력으로 고정한다. */
object ProjectProfile:
  val scalaVersion: String = "3.8.4"
  val scalaCliVersion: String = "1.15.0"
  val jdkRelease: Int = 25
