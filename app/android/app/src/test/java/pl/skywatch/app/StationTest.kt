package pl.skywatch.app

import org.junit.Assert.assertEquals
import org.junit.Test

/** Siatka na telefonie musi dawać te same środki kratek co coarsen() w hub.py. */
class StationTest {
    @Test fun sameGridAsServer() {
        // wartości policzone przez hub.coarsen: round((floor(x / step) + 0.5) * step, 5)
        val c = Station.cell(52.2297, 21.0122)
        assertEquals(52.23015, c[0], 1e-9)
        assertEquals(21.0122, c[1], 1e-9)
        val d = Station.cell(50.0614, 19.9366)
        assertEquals(50.06205, d[0], 1e-9)
        assertEquals(19.9386, d[1], 1e-9)
    }

    @Test fun hysteresisDistances() {
        assertEquals(300.0, Station.meters(52.0, 21.0, 52.0 + 300 / 111_320.0, 21.0), 0.5)
    }
}
