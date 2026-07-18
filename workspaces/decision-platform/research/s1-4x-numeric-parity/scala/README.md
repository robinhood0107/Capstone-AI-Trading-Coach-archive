# S1.4X Scala candidate

Scala 3.8.4/JDK 25 기반의 격리 numeric parity candidate다. Python/JAX나 다른 candidate를
호출하지 않고, frozen neutral request를 별도 JVM process에서 읽어 순수 core 결과를 쓴다.

`project.scala`는 dependency와 stable hard compiler profile의 단일 입력이다. Profile A가
기본값이며 B/C는 각각 전체 correctness와 동결 selector를 통과하기 전에는 선택하지 않는다.
