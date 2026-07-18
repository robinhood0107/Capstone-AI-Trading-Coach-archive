package ai.trading.coach.s14x.shell

import ai.trading.coach.s14x.core.StableError
import java.nio.file.Files
import munit.FunSuite

final class ContractShellSuite extends FunSuite:
  test("strict parser는 duplicate key와 decimal exact integer를 구분한다"):
    assert(
      ContractDecoder
        .decode(
          """{"schemaVersion":"s1.4x-request-v1","requestId":"a","requestId":"b","cases":[]}"""
        )
        .isLeft
    )
    val decimal =
      """{"schemaVersion":"s1.4x-request-v1","requestId":"a","cases":[{"fixtureId":"f","functionId":"cagr","arguments":{"prices":[100,101],"periods_per_year":1000.0}}]}"""
    val request = ContractDecoder.decode(decimal)
    request match
      case Right(value) =>
        val result = CandidateRunner.execute(value, Files.createTempDirectory("s14x-fixtures"))
        result match
          case Right(batch) =>
            assertEquals(batch.results.size, 1)
            val first = batch.results.take(1).foldLeft(Option.empty[CandidateCaseResult]) {
              (_, item) => Some(item)
            }
            assertEquals(
              first.flatMap(_.errorCode),
              Some(StableError.PeriodsPerYearInvalid.code),
            )
          case Left(error) => fail(s"semantic case는 transport error가 아니어야 한다: ${error.code}")
      case Left(error) => fail(s"request envelope는 valid여야 한다: ${error.code}")

  test("recursive normalizer는 nested -0.0을 positive zero로 바꾼다"):
    val normalized = JsonSupport.normalizeNumberTree(
      Map("vector" -> Vector(-0.0, 1.0), "nested" -> Map("value" -> -0.0))
    )
    val encoded = JsonSupport.encode(normalized)
    assert(!encoded.contains("-0.0"))

  test("stable error registry는 19+13 exact code를 가진다"):
    assertEquals(StableError.values.length, 32)
    assertEquals(StableError.values.map(_.code).toSet.size, 32)
