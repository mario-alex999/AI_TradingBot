import { describe, expect, it } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import NewsImpactPage from '@/pages/NewsImpactPage';
import { NAV_GROUPS } from '@/config/navigation';
import { getNewsImpact } from '@/services/intelligenceService';
import { mockApi } from './mockApi';
import { renderWithProviders } from './render';

const inHours = (h: number) => new Date(Date.now() + h * 3_600_000).toISOString();
const hist = (avg: number, followed: number) => ({ samples: 8, avgMovePct: avg, upPct: avg > 0 ? 75 : 25, followedRulePct: followed, minutes: 60 });

const scenarios = (eurAbove: 'up' | 'down') => [
  { key: 'above', label: 'Above forecast', currencyEffect: 'bullish', instruments: [
    { instrument: 'EURUSD', direction: eurAbove, explanation: 'x', history: hist(-0.21, 88) },
    { instrument: 'US30', direction: 'mixed', explanation: 'y', history: null },
  ] },
  { key: 'inline', label: 'In line', currencyEffect: 'neutral', instruments: [
    { instrument: 'EURUSD', direction: null, explanation: 'x', history: null },
    { instrument: 'US30', direction: null, explanation: 'y', history: null },
  ] },
  { key: 'below', label: 'Below forecast', currencyEffect: 'bearish', instruments: [
    { instrument: 'EURUSD', direction: 'up', explanation: 'x', history: hist(0.12, 70) },
    { instrument: 'US30', direction: 'mixed', explanation: 'y', history: null },
  ] },
];

const base = {
  country: 'US', category: 'inflation', subcategory: 'cpi', importance: 'high', timePrecision: 'exact', unit: '%', sourceUrl: null,
  instruments: [{ instrument: 'EURUSD', why: null }, { instrument: 'US30', why: null }], polarity: 1,
  rule: 'A higher figure than expected is conventionally supportive of the currency.', historySamples: 8,
};
const UPCOMING = {
  ...base, id: 2, name: 'Core CPI (MoM)', currency: 'USD', scheduledAt: inHours(3), status: 'scheduled', released: false,
  actual: null, consensus: 0.3, previous: 0.2, expectation: { vsPrevious: 'higher', text: 'The forecast (0.3) is higher than the previous figure (0.2).' },
  scenarios: scenarios('down'), outcome: null,
};
const RELEASED = {
  ...base, id: 1, name: 'Nonfarm Payrolls', currency: 'USD', scheduledAt: inHours(-1), status: 'released', released: true,
  actual: 250000, consensus: 180000, previous: 150000, unit: null, expectation: null, scenarios: scenarios('down'),
  outcome: { scenario: 'above', surprise: 70000, surprisePct: 38.89, currencyEffect: 'bullish',
    instruments: [{ instrument: 'EURUSD', expected: 'down', movePct: -0.34, minutesMeasured: 60, followedRule: true }] },
};
const REPORT = {
  generatedAt: inHours(0), method: 'news_impact_v1', reactionMinutes: 60, disclaimer: 'Conditional scenarios and measured history, not a forecast.',
  window: { start: inHours(-24), end: inHours(168) }, importance: ['high'], nextEvent: UPCOMING, events: [RELEASED, UPCOMING],
  headlines: [{ id: 9, headline: 'ECB raises interest rates by 25 basis points', source: 'ECB', url: 'https://example.org', publishedAt: inHours(-2),
    category: 'central_bank', currencies: ['EUR'], tone: { kind: 'policy_tone', label: 'hawkish', terms: ['raises interest rates'] },
    effect: { currencies: ['EUR'], bias: 'bullish' }, instruments: [{ instrument: 'EURUSD', direction: 'up', explanation: 'EUR is the base currency' }] }],
};

describe('News Impact', () => {
  it('is in the Intelligence navigation', () => {
    expect(NAV_GROUPS.flatMap((g) => g.items).some((i) => i.path === '/news' && i.label === 'News Impact')).toBe(true);
  });

  it('shows the next release, the scenarios with measured history, and the released outcome', async () => {
    mockApi({ 'GET /intelligence/news-impact': REPORT });
    renderWithProviders(<NewsImpactPage />, { mock: false });
    expect(await screen.findByText('Next high-impact release')).toBeInTheDocument();
    expect(screen.getByText(REPORT.disclaimer)).toBeInTheDocument();

    const [released, upcoming] = screen.getAllByTestId('impact-event');
    // The latest release opens with its outcome: above forecast, USD bullish, EURUSD fell as the rule expects.
    const outcome = within(released!).getByTestId('outcome');
    expect(outcome).toHaveTextContent('above the forecast');
    expect(outcome).toHaveTextContent('USD bullish');
    expect(outcome).toHaveTextContent('Moved -0.34% in 60m · as expected');

    // The upcoming event shows the expectation and, per scenario, direction + measured history.
    expect(within(upcoming!).getByText(/forecast \(0.3\) is higher than the previous/)).toBeInTheDocument();
    expect(within(upcoming!).getByText('+0.12%')).toBeInTheDocument();
    expect(within(upcoming!).getAllByText('Mixed').length).toBeGreaterThan(0);
    expect(within(upcoming!).getAllByText('No stored history').length).toBeGreaterThan(0);

    expect(screen.getByText('ECB raises interest rates by 25 basis points')).toBeInTheDocument();
    expect(screen.getByText('EUR bullish')).toBeInTheDocument();
  });

  it('collapses and expands an event', async () => {
    mockApi({ 'GET /intelligence/news-impact': REPORT });
    renderWithProviders(<NewsImpactPage />, { mock: false });
    const [released] = await screen.findAllByTestId('impact-event');
    const toggle = within(released!).getByRole('button', { expanded: true });
    fireEvent.click(toggle);
    expect(within(released!).queryByTestId('outcome')).not.toBeInTheDocument();
  });

  it('never invents news in demo mode', async () => {
    expect((await getNewsImpact({ mock: true }, { importance: 'high', hoursAhead: 168 })).data).toBeNull();
    renderWithProviders(<NewsImpactPage />);
    expect(await screen.findByText('No news data')).toBeInTheDocument();
  });
});
