package s1_4x.scalafix

import scala.meta._
import scalafix.v1._

final case class ForbiddenSemanticSymbol(
    tree: Tree,
    policySymbol: String,
    resolvedSymbol: String,
) extends Diagnostic {
  override def position: Position = tree.pos
  override def message: String =
    s"forbidden semantic symbol: $policySymbol resolved=$resolvedSymbol"
}

/**
 * Frozen source policy의 fully-qualified symbol을 SemanticDB로 검사한다.
 * 직접 이름뿐 아니라 inferred `.apply` 같은 compiler synthetic도 같은 matcher로 확인한다.
 */
final class S1_4XForbiddenSymbols
    extends SemanticRule("S1_4XForbiddenSymbols") {
  private val policyForbiddenSymbols: List[String] = List(
    "scala.Predef.require",
    "scala.Predef.assert",
    "scala.Predef.assume",
    "scala.sys.error",
    "scala.Option.get",
    "scala.util.Try.get",
    "scala.util.Either.LeftProjection.get",
    "scala.util.Either.RightProjection.get",
    "scala.collection.IterableOps.head",
    "scala.collection.IterableOps.tail",
    "scala.collection.IterableOps.init",
    "scala.collection.IterableOps.last",
    "scala.collection.IterableOnceOps.reduce",
    "scala.collection.IterableOnceOps.reduceLeft",
    "scala.collection.IterableOnceOps.reduceRight",
    "scala.collection.IterableOnceOps.max",
    "scala.collection.IterableOnceOps.min",
    "scala.collection.IterableOnceOps.maxBy",
    "scala.collection.IterableOnceOps.minBy",
    "scala.collection.MapOps.apply",
    "scala.collection.SeqOps.apply",
    "scala.Array.apply",
    "scala.Array.update",
    "scala.collection.Iterator.next",
    "java.lang.Integer.parseInt",
    "java.lang.Long.parseLong",
    "java.lang.Double.parseDouble",
    "scala.StringOps.toInt",
    "scala.StringOps.toLong",
    "scala.StringOps.toDouble",
  )
  private val additionalForbiddenSymbols: List[(String, String)] = List(
    "java.lang.Math.fma" -> "java.lang.Math.fma",
    "java.lang.System.load" -> "java.lang.System.load",
    "java.lang.System.loadLibrary" -> "java.lang.System.loadLibrary",
    "scala.Float" -> "scala.Float",
    "scala.Conversion" -> "scala.Conversion",
    "scala.annotation.internal.RuntimeChecked" ->
      "scala.annotation.internal.RuntimeChecked",
    "scala.language.implicitConversions" -> "scala.language.implicitConversions",
    "scala.language.experimental" -> "scala.language.experimental",
    "scala.language.experimental" -> "scala.language.experimental.betterFors",
  )
  private val forbiddenMatchers: List[(String, SymbolMatcher)] =
    policyForbiddenSymbols.map(name => name -> SymbolMatcher.normalized(name)) ++
      additionalForbiddenSymbols.map { case (policyName, resolvedName) =>
        policyName -> SymbolMatcher.normalized(resolvedName)
      }
  private val forbiddenPrefixes: List[(String, String)] = List(
    "scala.collection.mutable" -> "scala/collection/mutable/",
    "scala.collection.parallel" -> "scala/collection/parallel/",
    "java.util.concurrent" -> "java/util/concurrent/",
    "jdk.incubator.vector" -> "jdk/incubator/vector/",
    "scala.scalanative" -> "scala/scalanative/",
  )

  private def matchPolicy(symbol: Symbol): Option[String] = {
    if (symbol == Symbol.None) None
    else {
      forbiddenMatchers
        .collectFirst {
          case (policyName, matcher) if matcher.matches(symbol) => policyName
        }
        .orElse(
          forbiddenPrefixes.collectFirst {
            case (policyName, prefix) if symbol.value.startsWith(prefix) =>
              policyName
          }
        )
    }
  }

  private def symbolPatches(tree: Tree, symbols: List[Symbol]): List[Patch] =
    symbols.distinct.flatMap { symbol =>
      matchPolicy(symbol).map(policyName =>
        Patch.lint(ForbiddenSemanticSymbol(tree, policyName, symbol.value))
      )
    }

  override def fix(implicit document: SemanticDocument): Patch = {
    val direct = document.tree.collect {
      case name: Name => symbolPatches(name, List(name.symbol))
    }.flatten
    val inferred = document.tree.collect {
      case term: Term =>
        symbolPatches(term, term.synthetics.flatMap(_.symbol))
    }.flatten
    (direct ++ inferred).asPatch
  }
}
