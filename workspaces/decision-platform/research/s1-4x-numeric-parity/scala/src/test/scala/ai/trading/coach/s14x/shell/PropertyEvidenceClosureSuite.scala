package ai.trading.coach.s14x.shell

import ai.trading.coach.s14x.core.FrozenPropertyPlan
import java.nio.file.Files
import java.nio.file.Path
import munit.FunSuite
import scala.jdk.CollectionConverters.*

final class PropertyEvidenceClosureSuite extends FunSuite:
  private val s1Root =
    Path
      .of("workspaces/decision-platform/research/s1-4x-numeric-parity")
      .toAbsolutePath
      .normalize()
  private val frozenSeeds =
    Vector[Long](
      0,
      1,
      2,
      3,
      5,
      8,
      13,
      21,
      34,
      55,
      89,
      144,
      233,
      377,
      610,
      987,
      1597,
      2584,
      4181,
      6765,
      10946,
      17711,
      28657,
      46368,
    )

  test("property evidence는 frozen 24개 seed의 exact order를 보존한다"):
    val seedRoot =
      ContractDecoder.mapper.readTree(
        Files.readString(
          s1Root.resolve("contract/fixtures/property/property-seeds.v1.json")
        )
      )
    val seeds = seedRoot.path("seeds").elements().asScala.toVector.map(_.longValue())
    assertEquals(seeds, frozenSeeds)

  test("property evidence는 plan과 registered 25개 ID의 exact closure를 보존한다"):
    val planRoot =
      ContractDecoder.mapper.readTree(
        Files.readString(s1Root.resolve("contract/property-plan.v1.json"))
      )
    val expected =
      planRoot
        .path("properties")
        .elements()
        .asScala
        .toVector
        .map(_.path("propertyId").textValue())
    val prefix = "s1.4x-frozen-property-plan."
    val registered = FrozenPropertyPlan.properties.toVector.map { case (name, _) =>
      if name.startsWith(prefix) then name.drop(prefix.length) else name
    }
    assertEquals(expected.size, 25)
    assertEquals(registered, expected)
