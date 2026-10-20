# HANDOFF: pipeline Onshape → SOFA → optymalizacja

Dokument przekazania dla Claude Code. Stan: research zakończony (2026-10-20), kodu jeszcze nie ma.

## Cel

Parametryczny model w **Onshape** → automatyczny eksport geometrii → symulacja w **SOFA Framework** (FEM) → odczyt wyników → pętla optymalizacji parametrów modelu.

Użytkownik: młody inżynier mechatronik, uczy się. Tłumacz decyzje prosto, nazywaj ryzyka wprost, nie przytakuj. Jeśli coś jest niepewne, powiedz to.

## Decyzje już podjęte

1. **Zwykły deterministyczny skrypt Python, nie MCP w pętli.** LLM w środku setek iteracji = koszt, latencja, niedeterminizm. MCP (np. `hedless/onshape-mcp`) tylko opcjonalnie na końcu jako warstwa sterowania.
2. **Parametry przez Onshape Configurations, nie przez zmienne Part Studio.** Zmienne Part Studio nie mają prostego POST-a (trzeba `/features`, wolne). Configurations: jedno wywołanie `updateConfiguration`.
3. **SOFA przez `import Sofa` (SofaPython3) i własną pętlę `Sofa.Simulation.animate`.** Nie przez subprocess `runSofa`.
4. **Optymalizator: Optuna** (tak robi `SofaDefrost/SoftRobots.DesignOptimization`, precedens opublikowany).

## Architektura

```
Optuna (parametry)
  → onshape_client.py: encode configuration → export STL
  → mesher.py: STL → siatka tetraedryczna (.msh / .vtk)
  → sofa_sim.py: scena SOFA, animate, odczyt wyników
  → objective.py: wyniki → koszt (kara przy błędzie)
  → Optuna
```

Proponowana struktura repo:

```
onshape-sofa-opt/
  pyproject.toml
  .env.example          # ONSHAPE_ACCESS_KEY, ONSHAPE_SECRET_KEY (NIGDY nie commitować .env)
  src/pipeline/
    onshape_client.py
    mesher.py
    sofa_sim.py
    objective.py
    optimize.py
  scripts/              # ręczne kroki 1-4 z faz poniżej
  runs/                 # cache STL/mesh/wyniki per trial, w .gitignore
  tests/
```

## Fakty zweryfikowane (w ≥2 źródłach)

- Eksport STL i Parasolid z Part Studio to **jedno wywołanie**; STEP jest asynchroniczny (translation → polling → download).
- Do eksportu skonfigurowanego modelu **zawsze** podawaj parametr `configuration`, zakodowany przez endpoint `encodeConfigurationMap` / `getConfigurationEncoding`. Nie składaj stringa ręcznie (przy wielu parametrach Onshape zwraca klucz do zapisanej konfiguracji). Znany problem z forum: eksport API potrafi nie zgadzać się z UI, gdy konfiguracja nie jest jawna.
- Translacje wymagają uprawnień read **i write** na kluczu API (403 przy samym read).
- Jednostki STL z API: sprawdź (forum: potrafi wyjść w metrach i z niską rozdzielczością). Ustaw jawnie `units`, `scale` i jakość/granularność.
- SOFA: `Sofa.Simulation.animate(root, dt)` wywoływane z zwykłego Pythona działa; `runSofa -g batch` istnieje, ale nie służy do krokowania programowego.
- Do FEM potrzebne są **tetraedry** (`TetrahedronFEMForceField`). Loadery: `MeshSTLLoader` (powierzchnia), `MeshGmshLoader` (objętość, .msh).
- Przykład STL → tetra z `pygalmesh` jest w SofaPython3 (PR #69, `splib/meshing/pygalmesh_sofa.py`).
- Cienkie/wydłużone trójkąty w STL potrafią wywalić generowanie siatki, więc meshing musi być w try/except z karą w koszcie.

## Fakty NIEzweryfikowane (sprawdź na początku, nie zgaduj)

- Zgodna para wersji SOFA ↔ Python na maszynie użytkownika (dokumentacja SofaPython3, którą widziałem, wspomina Python 3.8, może być przestarzała). **Zacznij od `import Sofa` na działającej instalacji.**
- Czy `SoftRobots.DesignOptimization` da się podpiąć pod zewnętrzną geometrię bez przepisywania modułu konfiguracji (czytałem opisy, nie kod). Użyj go jako wzorca struktury, nie zakładaj, że jest drop-in.
- Pełne limity API Onshape dla konta użytkownika. Wiadomo tylko: od 10.10.2025 są roczne limity zależne od typu konta (EDU Student Free: 2 500/użytkownika), plus rate limit per endpoint (429, nagłówki `X-Rate-Limit-Remaining`, `Retry-After`).
- Brak znalezionego MCP dla SOFA Framework (wyniki "sofa-mcp" dotyczą Stack Overflow for Agents). Nie buduj na założeniu, że istnieje.

## Fazy i kryteria akceptacji

Rób po kolei, każdą fazę pokaż użytkownikowi zanim przejdziesz dalej.

**Faza 0: środowisko.** Python venv, `import Sofa` działa, `onshape-rest-api-client` (lub własny cienki wrapper `requests` z podpisem HMAC) zainstalowany, klucze w `.env`.
Akceptacja: skrypt wypisuje wersję SOFA i listę dokumentów Onshape użytkownika.

**Faza 1: Onshape.** Funkcja `export_stl(params: dict) -> Path`: `updateConfiguration` lub podanie zakodowanej konfiguracji → eksport STL z jawnymi jednostkami.
Akceptacja: dwa różne zestawy parametrów dają dwa różne STL; bounding box zgadza się z wymiarami z UI. Loguj `X-Rate-Limit-Remaining` i liczbę wykonanych wywołań.

**Faza 2: meshing.** `make_tet_mesh(stl) -> Path` (pygalmesh lub Gmsh Python API). Parametry gęstości siatki jako argumenty.
Akceptacja: siatka ładuje się w SOFA; funkcja rzuca czytelny wyjątek przy porażce.

**Faza 3: SOFA.** `run_sim(mesh, material, bc, loads) -> dict` z wynikami (np. maks. przemieszczenie, maks. von Mises, masa). Scena budowana programowo, pętla `animate` do ustalenia (albo N kroków, albo test zbieżności).
Akceptacja: **walidacja na przypadku z rozwiązaniem analitycznym** (np. belka utwierdzona, ugięcie `F·L³/(3EI)`). Bez tego żaden wynik optymalizacji nie ma wartości. Ostrzeż użytkownika o ograniczeniach liniowego FEM i `method="large"` vs `small`.

**Faza 4: funkcja celu.** `evaluate(params) -> float | tuple`. Przechwytuje wyjątki z kroków 1-3 i zwraca karę. Cache po haszu parametrów (oszczędza wywołania API).
Akceptacja: 5 ręcznych wywołań, wyniki powtarzalne.

**Faza 5: optymalizacja.** Optuna, najpierw 1-2 parametry, ~20 trials. Zapis study do SQLite. Wykres historii i (jeśli wielokryterialnie) frontu Pareto.
Akceptacja: koszt maleje, najlepszy zestaw parametrów da się odtworzyć i zobaczyć w Onshape.

**Faza 6 (opcjonalna): MCP.** Cienki serwer MCP z narzędziami `run_trial`, `get_best`, `list_trials`, żeby sterować z Claude. Nie przenoś pętli do LLM.

## Ograniczenia i ryzyka

- **Budżet API Onshape**: policz wywołania na trial (update config + encode + export, ewentualnie polling). Przed fazą 5 pokaż użytkownikowi szacunek `trials × wywołania` względem jego limitu.
- Eksport chmurowy to sekundy na trial; nie planuj tysięcy iteracji.
- Część kombinacji parametrów da zdegenerowaną geometrię. Waliduj zakresy w Onshape (limity w konfiguracji) i w kodzie.
- Sekrety: tylko zmienne środowiskowe / `.env`, `.env` w `.gitignore`.
- Dokumentacja Onshape API bywa uboga (forum: uprawnienia, parametry). Gdy endpoint zachowuje się dziwnie, sprawdź w Glassworks API Explorer.

## Pytania do użytkownika (zadaj na starcie, nie zgaduj)

1. Co jest modelem i co optymalizujemy (soft robot, uchwyt, belka)? Jaki cel: ugięcie, masa, naprężenie?
2. Jaki plan Onshape (Free/EDU/Pro)? Ile wywołań API zostało?
3. Jaki system operacyjny i jaka instalacja SOFA (binarka, pip, build ze źródeł)? Jaka wersja Pythona?
4. Materiał i warunki brzegowe (moduł Younga, Poisson, podparcia, obciążenia)?
5. Czy model w Onshape ma już Configurations? Jeśli nie, które wymiary mają być parametrami?

## Źródła

- Onshape Import & Export: https://onshape-public.github.io/docs/api-adv/translation
- Onshape Configurations: https://onshape-public.github.io/docs/api-adv/configs
- Onshape API Limits: https://onshape-public.github.io/docs/auth/limits
- Forum, aktualizacja wielu zmiennych: https://forum.onshape.com/discussion/comment/128270/
- Forum, parametric study (configuration w eksporcie): https://forum.onshape.com/discussion/comment/56103
- SofaPython3 docs: https://sofapython3.readthedocs.io/en/latest/content/Installation.html
- Sofa.Simulation.animate: https://sofapython3.readthedocs.io/en/latest/content/modules/Sofa/generated/Sofa.Simulation/functions/Sofa.Simulation.animate.html
- SOFA forum, krokowanie bez GUI: https://www.sofa-framework.org/community/forum/topic/using-sofa-in-the-context-of-reinforcement-leaening/
- SoftRobots.DesignOptimization: https://github.com/SofaDefrost/SoftRobots.DesignOptimization
- SofaPython3 PR #69 (STL → tetra): https://github.com/sofa-framework/SofaPython3/pull/69/files
- Onshape MCP (opcjonalnie): https://github.com/hedless/onshape-mcp
