import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class SeedTest {
    @Test
    void resolvesTheFrozenTestRuntime() {
        assertThat(true).isTrue();
    }
}
