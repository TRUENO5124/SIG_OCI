#include "stm32f10x.h"
#include <stdint.h>

#define SYSCLK_HZ             72000000UL
#define UART_BAUD             115200UL

#define PWM_TOP               999U
#define GEN_UPDATE_HZ         40000UL
#define GEN_MIN_HZ            1U
#define GEN_MAX_HZ            10000U

#define CAP_MAX_SAMPLES       512U
#define CAP_MIN_RATE_HZ       10U
#define CAP_MAX_RATE_HZ       20000U
#define VREF_MV               3300U

#define TIM_CCMR1_OC1_PWM1    (TIM_CCMR1_OC1M_1 | TIM_CCMR1_OC1M_2)
#define ADC_EXTSEL_SWSTART    (ADC_CR2_EXTSEL_0 | ADC_CR2_EXTSEL_1 | ADC_CR2_EXTSEL_2)

typedef enum
{
    WAVE_OFF = 0,
    WAVE_SINE,
    WAVE_SQUARE,
    WAVE_TRIANGLE,
    WAVE_SAW
} WaveType;

static volatile WaveType g_wave = WAVE_OFF;
static volatile uint32_t g_phase_acc = 0;
static volatile uint32_t g_phase_step = 0;
static volatile uint16_t g_amp_percent = 50;
static volatile uint16_t g_offset_percent = 50;
static volatile uint32_t g_tick_40k = 0;

static char g_line[96];
static uint32_t g_line_len = 0;
static uint16_t g_capture[CAP_MAX_SAMPLES];

static const uint16_t g_sine64[64] =
{
    500, 549, 598, 645, 691, 736, 778, 817,
    854, 887, 916, 941, 962, 978, 990, 998,
    1000, 998, 990, 978, 962, 941, 916, 887,
    854, 817, 778, 736, 691, 645, 598, 549,
    500, 451, 402, 355, 309, 264, 222, 183,
    146, 113, 84, 59, 38, 22, 10, 2,
    0, 2, 10, 22, 38, 59, 84, 113,
    146, 183, 222, 264, 309, 355, 402, 451
};

static void clocks_init(void);
static void gpio_init(void);
static void uart1_init(void);
static void adc1_init(void);
static void pwm_init(void);
static void gen_timer_init(void);
static void systick_delay_init(void);
static void uart_putc(char ch);
static void uart_puts(const char *s);
static void uart_put_u32(uint32_t value);
static void uart_put_line(const char *s);
static uint16_t adc1_read(void);
static void delay_us(uint32_t us);
static void poll_uart_line(void);
static void handle_line(char *line);
static void cmd_info(void);
static void cmd_gen(char *args);
static void cmd_capture(char *args);
static char *next_token(char **cursor);
static char upper_ascii(char ch);
static uint8_t token_eq(const char *a, const char *b);
static uint8_t parse_u32(const char *s, uint32_t *out);
static WaveType parse_wave(const char *s);
static void generator_set(WaveType wave, uint32_t freq_hz, uint32_t amp, uint32_t offset);
static uint16_t generator_next_duty(void);
void TIM2_IRQHandler(void);

int main(void)
{
    clocks_init();
    gpio_init();
    uart1_init();
    systick_delay_init();
    adc1_init();
    pwm_init();
    gen_timer_init();

    uart_put_line("READY STM32F103_SIGSCOPE");
    uart_put_line("PINS GEN=PA6_PWM SCOPE=PA0_ADC UART=PA9_PA10");

    while (1)
    {
        poll_uart_line();
    }
}

static void clocks_init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_AFIOEN |
                    RCC_APB2ENR_IOPAEN |
                    RCC_APB2ENR_IOPCEN |
                    RCC_APB2ENR_USART1EN |
                    RCC_APB2ENR_ADC1EN;

    RCC->APB1ENR |= RCC_APB1ENR_TIM2EN |
                    RCC_APB1ENR_TIM3EN;

    RCC->CFGR &= ~RCC_CFGR_ADCPRE;
    RCC->CFGR |= RCC_CFGR_ADCPRE_DIV6;
}

static void gpio_init(void)
{
    uint32_t cr;

    cr = GPIOA->CRL;
    cr &= ~((0x0FUL << (0U * 4U)) | (0x0FUL << (6U * 4U)));
    cr |=  (0x0BU << (6U * 4U));       /* PA6: TIM3_CH1 alternate push-pull. */
    GPIOA->CRL = cr;

    cr = GPIOA->CRH;
    cr &= ~((0x0FUL << ((9U - 8U) * 4U)) | (0x0FUL << ((10U - 8U) * 4U)));
    cr |=  (0x0BU << ((9U - 8U) * 4U));  /* PA9: USART1 TX alternate push-pull. */
    cr |=  (0x04U << ((10U - 8U) * 4U)); /* PA10: USART1 RX floating input. */
    GPIOA->CRH = cr;

    cr = GPIOC->CRH;
    cr &= ~(0x0FUL << ((13U - 8U) * 4U));
    cr |=  (0x02U << ((13U - 8U) * 4U)); /* PC13: 2 MHz push-pull status LED. */
    GPIOC->CRH = cr;
    GPIOC->BSRR = (1U << 13);
}

static void uart1_init(void)
{
    USART1->BRR = (uint16_t)((SYSCLK_HZ + (UART_BAUD / 2U)) / UART_BAUD);
    USART1->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;
}

static void adc1_init(void)
{
    ADC1->CR2 = ADC_CR2_ADON;
    delay_us(10);

    ADC1->CR2 |= ADC_CR2_RSTCAL;
    while ((ADC1->CR2 & ADC_CR2_RSTCAL) != 0U)
    {
    }

    ADC1->CR2 |= ADC_CR2_CAL;
    while ((ADC1->CR2 & ADC_CR2_CAL) != 0U)
    {
    }

    ADC1->SMPR2 &= ~ADC_SMPR2_SMP0;
    ADC1->SMPR2 |= (5U << 0);          /* 55.5 ADC cycles on channel 0. */
    ADC1->SQR1 = 0;
    ADC1->SQR3 = 0;                    /* First regular conversion: ADC channel 0. */
    ADC1->CR2 = ADC_CR2_ADON | ADC_CR2_EXTTRIG | ADC_EXTSEL_SWSTART;
}

static void pwm_init(void)
{
    TIM3->PSC = 0;
    TIM3->ARR = PWM_TOP;
    TIM3->CCR1 = 0;
    TIM3->CCMR1 = TIM_CCMR1_OC1_PWM1 | TIM_CCMR1_OC1PE;
    TIM3->CCER = TIM_CCER_CC1E;
    TIM3->CR1 = TIM_CR1_ARPE;
    TIM3->EGR = TIM_EGR_UG;
    TIM3->CR1 |= TIM_CR1_CEN;
}

static void gen_timer_init(void)
{
    TIM2->PSC = 71;                    /* 72 MHz timer clock -> 1 MHz. */
    TIM2->ARR = (uint16_t)((1000000UL / GEN_UPDATE_HZ) - 1UL);
    TIM2->DIER = TIM_DIER_UIE;
    TIM2->EGR = TIM_EGR_UG;
    NVIC_EnableIRQ(TIM2_IRQn);
    TIM2->CR1 = TIM_CR1_CEN;
}

static void systick_delay_init(void)
{
    SysTick->CTRL = 0;
    SysTick->LOAD = 0x00FFFFFFUL;
    SysTick->VAL = 0;
    SysTick->CTRL = SysTick_CTRL_CLKSOURCE_Msk | SysTick_CTRL_ENABLE_Msk;
}

static void uart_putc(char ch)
{
    while ((USART1->SR & USART_SR_TXE) == 0U)
    {
    }
    USART1->DR = (uint16_t)ch;
}

static void uart_puts(const char *s)
{
    while (*s != '\0')
    {
        uart_putc(*s++);
    }
}

static void uart_put_u32(uint32_t value)
{
    char buf[11];
    uint32_t i = 0;

    if (value == 0U)
    {
        uart_putc('0');
        return;
    }

    while ((value > 0U) && (i < sizeof(buf)))
    {
        buf[i++] = (char)('0' + (value % 10U));
        value /= 10U;
    }

    while (i > 0U)
    {
        uart_putc(buf[--i]);
    }
}

static void uart_put_line(const char *s)
{
    uart_puts(s);
    uart_puts("\r\n");
}

static uint16_t adc1_read(void)
{
    ADC1->CR2 |= ADC_CR2_SWSTART;
    while ((ADC1->SR & ADC_SR_EOC) == 0U)
    {
    }
    return (uint16_t)(ADC1->DR & 0x0FFFU);
}

static void delay_us(uint32_t us)
{
    uint32_t ticks;
    uint32_t start;

    while (us > 0U)
    {
        uint32_t chunk = (us > 200000U) ? 200000U : us;
        ticks = chunk * (SYSCLK_HZ / 1000000UL);
        start = SysTick->VAL;
        while (((start - SysTick->VAL) & 0x00FFFFFFUL) < ticks)
        {
        }
        us -= chunk;
    }
}

static void poll_uart_line(void)
{
    while ((USART1->SR & USART_SR_RXNE) != 0U)
    {
        char ch = (char)(USART1->DR & 0xFFU);

        if ((ch == '\r') || (ch == '\n'))
        {
            if (g_line_len > 0U)
            {
                g_line[g_line_len] = '\0';
                handle_line(g_line);
                g_line_len = 0;
            }
        }
        else if (g_line_len < (sizeof(g_line) - 1U))
        {
            g_line[g_line_len++] = ch;
        }
        else
        {
            g_line_len = 0;
            uart_put_line("ERR LINE_TOO_LONG");
        }
    }
}

static void handle_line(char *line)
{
    char *cursor = line;
    char *cmd = next_token(&cursor);

    if (cmd == 0)
    {
        return;
    }

    if (token_eq(cmd, "PING") != 0U)
    {
        uart_put_line("PONG STM32F103_SIGSCOPE");
    }
    else if (token_eq(cmd, "INFO") != 0U)
    {
        cmd_info();
    }
    else if (token_eq(cmd, "GEN") != 0U)
    {
        cmd_gen(cursor);
    }
    else if ((token_eq(cmd, "CAP") != 0U) || (token_eq(cmd, "CAPTURE") != 0U))
    {
        cmd_capture(cursor);
    }
    else if (token_eq(cmd, "HELP") != 0U)
    {
        uart_put_line("CMDS PING INFO GEN_OFF GEN_<WAVE>_<HZ>_<AMP>_<OFFSET> CAP_<N>_<RATE>");
    }
    else
    {
        uart_put_line("ERR BAD_CMD");
    }
}

static void cmd_info(void)
{
    uart_puts("INFO MCU=STM32F103C8T6 UART=USART1_115200 GEN=PA6_TIM3CH1_PWM SCOPE=PA0_ADC1IN0 ");
    uart_puts("GEN_HZ=");
    uart_put_u32(GEN_MIN_HZ);
    uart_putc('-');
    uart_put_u32(GEN_MAX_HZ);
    uart_puts(" CAP_MAX=");
    uart_put_u32(CAP_MAX_SAMPLES);
    uart_puts(" PORT=7897_SIM");
    uart_puts("\r\n");
}

static void cmd_gen(char *args)
{
    char *cursor = args;
    char *wave_s = next_token(&cursor);
    char *freq_s;
    char *amp_s;
    char *offset_s;
    uint32_t freq;
    uint32_t amp;
    uint32_t offset;
    WaveType wave;

    if ((wave_s != 0) && (token_eq(wave_s, "OFF") != 0U))
    {
        generator_set(WAVE_OFF, 0, 0, 50);
        uart_put_line("OK GEN OFF");
        return;
    }

    freq_s = next_token(&cursor);
    amp_s = next_token(&cursor);
    offset_s = next_token(&cursor);
    wave = parse_wave(wave_s);

    if ((wave == WAVE_OFF) ||
        (parse_u32(freq_s, &freq) == 0U) ||
        (parse_u32(amp_s, &amp) == 0U) ||
        (parse_u32(offset_s, &offset) == 0U))
    {
        uart_put_line("ERR GEN_USAGE GEN <SINE|SQUARE|TRIANGLE|SAW> <HZ> <AMP%> <OFFSET%>");
        return;
    }

    if ((freq < GEN_MIN_HZ) || (freq > GEN_MAX_HZ) || (amp > 100U) || (offset > 100U))
    {
        uart_put_line("ERR GEN_RANGE");
        return;
    }

    generator_set(wave, freq, amp, offset);

    uart_puts("OK GEN ");
    uart_puts(wave_s);
    uart_putc(' ');
    uart_put_u32(freq);
    uart_putc(' ');
    uart_put_u32(amp);
    uart_putc(' ');
    uart_put_u32(offset);
    uart_puts("\r\n");
}

static void cmd_capture(char *args)
{
    char *cursor = args;
    char *samples_s = next_token(&cursor);
    char *rate_s = next_token(&cursor);
    uint32_t samples;
    uint32_t rate;
    uint32_t period_us;
    uint32_t i;

    if ((parse_u32(samples_s, &samples) == 0U) ||
        (parse_u32(rate_s, &rate) == 0U))
    {
        uart_put_line("ERR CAP_USAGE CAP <SAMPLES> <RATE_HZ>");
        return;
    }

    if ((samples == 0U) || (samples > CAP_MAX_SAMPLES) ||
        (rate < CAP_MIN_RATE_HZ) || (rate > CAP_MAX_RATE_HZ))
    {
        uart_put_line("ERR CAP_RANGE");
        return;
    }

    period_us = 1000000UL / rate;
    if (period_us == 0U)
    {
        period_us = 1U;
    }

    for (i = 0; i < samples; ++i)
    {
        g_capture[i] = adc1_read();
        if (i + 1U < samples)
        {
            delay_us(period_us);
        }
    }

    uart_puts("DATA ");
    uart_put_u32(samples);
    uart_putc(' ');
    uart_put_u32(rate);
    uart_putc(' ');
    uart_put_u32(VREF_MV);
    uart_puts("\r\n");

    for (i = 0; i < samples; ++i)
    {
        uart_put_u32(g_capture[i]);
        if (i + 1U < samples)
        {
            uart_putc(',');
            if (((i + 1U) % 16U) == 0U)
            {
                uart_puts("\r\n");
            }
        }
    }

    uart_puts("\r\nEND\r\n");
}

static char *next_token(char **cursor)
{
    char *s;

    if ((cursor == 0) || (*cursor == 0))
    {
        return 0;
    }

    s = *cursor;
    while ((*s == ' ') || (*s == '\t'))
    {
        ++s;
    }

    if (*s == '\0')
    {
        *cursor = s;
        return 0;
    }

    *cursor = s;
    while ((**cursor != '\0') && (**cursor != ' ') && (**cursor != '\t'))
    {
        ++(*cursor);
    }

    if (**cursor != '\0')
    {
        **cursor = '\0';
        ++(*cursor);
    }

    return s;
}

static char upper_ascii(char ch)
{
    if ((ch >= 'a') && (ch <= 'z'))
    {
        ch = (char)(ch - ('a' - 'A'));
    }
    return ch;
}

static uint8_t token_eq(const char *a, const char *b)
{
    if ((a == 0) || (b == 0))
    {
        return 0;
    }

    while ((*a != '\0') && (*b != '\0'))
    {
        if (upper_ascii(*a) != upper_ascii(*b))
        {
            return 0;
        }
        ++a;
        ++b;
    }

    return (*a == '\0') && (*b == '\0');
}

static uint8_t parse_u32(const char *s, uint32_t *out)
{
    uint32_t value = 0;
    uint8_t seen = 0;

    if ((s == 0) || (out == 0))
    {
        return 0;
    }

    while ((*s >= '0') && (*s <= '9'))
    {
        value = (value * 10U) + (uint32_t)(*s - '0');
        seen = 1;
        ++s;
    }

    if ((*s != '\0') || (seen == 0U))
    {
        return 0;
    }

    *out = value;
    return 1;
}

static WaveType parse_wave(const char *s)
{
    if (token_eq(s, "SINE") != 0U)
    {
        return WAVE_SINE;
    }
    if (token_eq(s, "SQUARE") != 0U)
    {
        return WAVE_SQUARE;
    }
    if (token_eq(s, "TRIANGLE") != 0U)
    {
        return WAVE_TRIANGLE;
    }
    if (token_eq(s, "SAW") != 0U)
    {
        return WAVE_SAW;
    }
    return WAVE_OFF;
}

static void generator_set(WaveType wave, uint32_t freq_hz, uint32_t amp, uint32_t offset)
{
    __disable_irq();
    g_wave = wave;
    g_phase_acc = 0;
    g_phase_step = (uint32_t)(((uint64_t)freq_hz * 4294967296ULL) / GEN_UPDATE_HZ);
    g_amp_percent = (uint16_t)amp;
    g_offset_percent = (uint16_t)offset;
    if (wave == WAVE_OFF)
    {
        TIM3->CCR1 = 0;
    }
    __enable_irq();
}

static uint16_t generator_next_duty(void)
{
    uint32_t phase;
    uint32_t phase16;
    uint32_t raw;
    int32_t duty;

    if (g_wave == WAVE_OFF)
    {
        return 0;
    }

    g_phase_acc = g_phase_acc + g_phase_step;
    phase = g_phase_acc;
    phase16 = phase >> 16;

    switch (g_wave)
    {
        case WAVE_OFF:
            raw = 0;
            break;

        case WAVE_SINE:
            raw = g_sine64[(phase >> 26) & 0x3FU];
            break;

        case WAVE_SQUARE:
            raw = (phase < 0x80000000UL) ? 1000U : 0U;
            break;

        case WAVE_TRIANGLE:
            if (phase16 < 32768U)
            {
                raw = (phase16 * 1000U) / 32768U;
            }
            else
            {
                raw = ((65535U - phase16) * 1000U) / 32768U;
            }
            break;

        case WAVE_SAW:
            raw = (phase16 * 1000U) / 65536U;
            break;
    }

    duty = (int32_t)(g_offset_percent * 10U);
    duty += (((int32_t)raw - 500) * (int32_t)g_amp_percent) / 100;

    if (duty < 0)
    {
        duty = 0;
    }
    else if (duty > (int32_t)PWM_TOP)
    {
        duty = (int32_t)PWM_TOP;
    }

    return (uint16_t)duty;
}

void TIM2_IRQHandler(void)
{
    if ((TIM2->SR & TIM_SR_UIF) != 0U)
    {
        TIM2->SR = (uint16_t)~TIM_SR_UIF;
        TIM3->CCR1 = generator_next_duty();

        ++g_tick_40k;
        if ((g_tick_40k % (GEN_UPDATE_HZ / 2U)) == 0U)
        {
            GPIOC->ODR ^= (1U << 13);
        }
    }
}
