> Historical documentation from the supplied UI build. Not a statement of current verification or security. The root README, BUSINESS_READINESS, DEPLOYMENT, SECURITY and TEST_REPORT documents take precedence.

# Test report - DataForge workspace improvement build

## Result

**37 passed, 0 failed.** No uncaught JavaScript page errors were observed in the added suite.

## Environment and method

- Node.js 22.16.0 and headless Chromium, driven through the available Playwright browser tooling.
- The environment-provided Playwright core was 1.57.0-beta-1764944708000. The project's existing pinned development dependency (1.63.0) was not changed; installing that version was blocked by unavailable registry access.
- Desktop viewport: 1440 x 1000. Phone viewport: 390 x 844; narrow-layout check: 320 x 700.
- Dark and light themes, plus a reduced-motion preference.
- The actual HTML application was loaded with Playwright setContent. External HTTPS requests were deliberately blocked for this offline UI suite.
- Preview checks used synthetic rows, not a successful database run or user-uploaded data.
- The execution-engine source block was compared with the supplied file and is unchanged.
- The extracted application JavaScript passed `node --check`.

## Not verified

This is not a full production certification. DuckDB-WASM query execution, the complete original smoke suite, generated-code execution in native engines or cloud warehouses, real-origin IndexedDB persistence, and encrypted-project round trips were not tested in this environment. The original tests remain included for a network-enabled environment. No claim is made that the complete application passes those suites.

The new browser suite also does not replace a full accessibility audit, cross-browser testing, or a large-data performance benchmark.

## Executed checks

1. **PASS** - Welcome opens without a blocking About dialog
2. **PASS** - Import XML opens the actual file picker
3. **PASS** - Sample card loads a real six-node mapping
4. **PASS** - Palette contains all 24 registered node types
5. **PASS** - Palette search filters by name
6. **PASS** - Palette search supports meaningful aliases
7. **PASS** - Empty palette search gives feedback and can be cleared
8. **PASS** - Keyboard Enter adds a transformation
9. **PASS** - Keyboard Space adds a transformation without scrolling
10. **PASS** - Repository jump reaches and focuses repository search
11. **PASS** - Command search has an accessible no-results state
12. **PASS** - Command palette recovers after a no-match search
13. **PASS** - Manual About dialog still opens and closes
14. **PASS** - Preview defaults to 100 rows, not every loaded row
15. **PASS** - Preview pagination preserves row numbering
16. **PASS** - Preview page-size control resets to page 1
17. **PASS** - Preview search keeps input focus while typing
18. **PASS** - Preview search explicitly handles zero matches
19. **PASS** - Sensitive values cannot be discovered through search
20. **PASS** - Column sorting is keyboard-accessible and numeric
21. **PASS** - Preview safely renders HTML-like cell values as text
22. **PASS** - Empty preview handles an empty result set
23. **PASS** - Workflow palette is searchable and keyboard-focusable
24. **PASS** - Palette query persists independently for mapping mode
25. **PASS** - Monitor stays below the resized header
26. **PASS** - Sample SQL generation still works for all seven dialects
27. **PASS** - Sample additions preserve existing mappings
28. **PASS** - Light-theme desktop layout has no document overflow
29. **PASS** - Dark-theme desktop layout has no document overflow
30. **PASS** - Reduced-motion preference disables transitions
31. **PASS** - Phone welcome has no horizontal overflow
32. **PASS** - Phone template cards load mappings
33. **PASS** - Phone library drawer opens below the header
34. **PASS** - Phone properties drawer opens and closes with Escape
35. **PASS** - Phone engine status remains accessible for retry
36. **PASS** - 320px viewport keeps primary header controls inside the screen
37. **PASS** - No uncaught browser JavaScript errors
