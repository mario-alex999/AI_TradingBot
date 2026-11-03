import { afterEach, describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { InternalAxiosRequestConfig } from 'axios';
import MarketIntelligencePage from '@/pages/MarketIntelligencePage';
import { EconomicCalendarPanel } from '@/components/intelligence/CalendarPanels';
import { MacroPanel } from '@/components/intelligence/MarketPanels';
import { getCalendar, getMarketIntelligence } from '@/services/intelligenceService';
import { apiClient } from '@/services/api';
import { mockApi, offlineAdapter } from './mockApi';
import { renderWithProviders } from './render';
import type { CalendarPage, EconomicEvent, MacroOverview } from '@/types/intelligence';

const originalAdapter = apiClient.defaults.adapter;
afterEach(() => {
  apiClient.defaults.adapter = originalAdapter;
});

// Shapes mirror the FastAPI responses; values are test fixtures, not provider data.
const event: EconomicEvent = {
  id: 7, eventUid: 'official_schedule:fed-2026-12-09', provider: 'official_schedule', providerEventId: 'fed-2026-12-09',
  name: 'Federal Reserve (FOMC) monetary policy decision', country: null, currency: 'USD', category: 'monetary_policy',
  subcategory: 'rate_decision', importance: 'high', importanceSource: 'rule', scheduledAt: '2026-12-09T19:00:00+00:00',
  originalScheduledAt: '2026-12-09T19:00:00+00:00', sourceTimezone: 'America/New_York', timePrecision: 'typical',
  actual: null, consensus: null, previous: null, revisedPrevious: null, unit: null, status: 'scheduled',
  sourceUrl: 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm', retrievedAt: '2026-11-03T17:47:00+00:00',
  affectedInstruments: [{ instrument: 'EURUSD', relation: 'direct', currency: 'USD', explanation: 'quote currency', direction: null }],
  qualityStatus: 'ok', minutesUntilRelease: 52000,
};
const partial = { complete: false, level: 'partial' as const, providers: ['official_schedule'], reason: 'No full economic-calendar provider is configured.' };
const page: CalendarPage = { items: [event], page: 1, pageSize: 25, total: 1, coverage: partial, asOf: '2026-11-03T18:00:00+00:00', pointInTime: false };

describe('Module 04 market intelligence UI', () => {
  it('never fabricates intelligence in demo mode and makes no request', async () => {
    let calls = 0;
    apiClient.defaults.adapter = (c) => {
      calls += 1;
      return offlineAdapter(c);
    };
    renderWithProviders(<MarketIntelligencePage />, { mock: true });
    expect(await screen.findAllByText('Unavailable in demo mode')).not.toHaveLength(0);
    expect(calls).toBe(0);
    const demo = await getMarketIntelligence({ mock: true }, 'EURUSD');
    expect(demo).toMatchObject({ data: null, source: 'demo' });
  });

  it('shows calendar events with partial-coverage warning and blank (not zero) missing values', async () => {
    mockApi({ 'GET /intelligence/calendar': page });
    renderWithProviders(<EconomicCalendarPanel />, { mock: false });
    const row = (await screen.findByText(event.name)).closest('tr') as HTMLElement;
    expect(within(row).getAllByText('—').length).toBeGreaterThanOrEqual(3); // actual, forecast, previous
    expect(within(row).queryByText('0')).toBeNull();
    expect(within(row).getByText('(typical)')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Calendar coverage: partial');
  });

  it('labels official-schedule coverage and lists its gaps instead of hiding them', async () => {
    const official = {
      complete: true, level: 'official' as const, providers: ['bls_ics', 'official_schedule'],
      reason: 'Official agency and central-bank calendars cover EUR, GBP, USD.',
      currencies: { USD: { covered: true, providers: ['bls_ics'], missing: [] }, GBP: { covered: false, providers: ['ons_releases'], missing: ['ons_releases'] } },
      gaps: { USD: ['ISM manufacturing / services PMI'] },
    };
    mockApi({ 'GET /intelligence/calendar': { ...page, coverage: official } });
    renderWithProviders(<EconomicCalendarPanel />, { mock: false });
    const note = (await screen.findByText('Calendar coverage: official schedules.')).closest('[role="status"]') as HTMLElement;
    expect(note).toHaveTextContent('Calendar coverage: official schedules');
    expect(note).toHaveTextContent('Not covered right now: GBP');
    expect(note).toHaveTextContent('ISM manufacturing / services PMI');
  });

  it('sends calendar filters as query parameters', async () => {
    let seen: Record<string, unknown> | undefined;
    mockApi({
      'GET /intelligence/calendar': (c: InternalAxiosRequestConfig) => {
        seen = c.params as Record<string, unknown>;
        return { status: 200, data: page };
      },
    });
    await getCalendar({ mock: false }, { range: 'today', currencies: ['USD', 'EUR'], importance: ['high'], instrument: 'XAUUSD' });
    expect(seen).toMatchObject({ range: 'today', currency: 'USD,EUR', importance: 'high', instrument: 'XAUUSD', page: 1 });
  });

  it('changing a filter refetches with the new value', async () => {
    const params: Record<string, unknown>[] = [];
    mockApi({
      'GET /intelligence/calendar': (c: InternalAxiosRequestConfig) => {
        params.push(c.params as Record<string, unknown>);
        return { status: 200, data: page };
      },
    });
    renderWithProviders(<EconomicCalendarPanel />, { mock: false });
    await screen.findByText(event.name);
    await userEvent.selectOptions(screen.getByLabelText('Importance'), 'high');
    await waitFor(() => expect(params.at(-1)).toMatchObject({ importance: 'high' }));
  });

  it('reports a disconnected backend instead of falling back to demo data', async () => {
    apiClient.defaults.adapter = offlineAdapter;
    renderWithProviders(<EconomicCalendarPanel />, { mock: false });
    expect(await screen.findByText("Backend disconnected")).toBeInTheDocument();
    expect(screen.queryByText('Unavailable in demo mode')).toBeNull();
  });

  it('shows unsourced central banks as not sourced rather than estimating a rate', async () => {
    const macro: MacroOverview = {
      asOf: '2026-11-03T18:00:00+00:00', note: 'Official sources only.', indicators: [],
      centralBanks: [
        { bank: 'fed', name: 'Federal Reserve', currency: 'USD', officialUrl: 'https://www.federalreserve.gov', rateSource: 'nyfed_effr_target', nextMeeting: null, status: 'ok', currentRate: 4, currentRateLower: 3.75, lastChangeEffective: '2026-09-17', previousRate: 3.75, latestDecision: null, source: 'nyfed_effr_target', stale: false },
        { bank: 'boj', name: 'Bank of Japan', currency: 'JPY', officialUrl: 'https://www.boj.or.jp', rateSource: null, nextMeeting: null, status: 'not_sourced', reason: 'No keyless official series configured', currentRate: null },
      ],
    };
    mockApi({ 'GET /intelligence/macro/overview': macro });
    renderWithProviders(<MacroPanel />, { mock: false });
    expect(await screen.findByText('3.75–4.00%')).toBeInTheDocument();
    const boj = screen.getByText('BOJ').closest('tr') as HTMLElement;
    expect(within(boj).getByText('Not Sourced')).toBeInTheDocument();
    expect(within(boj).queryByText(/%/)).toBeNull();
  });
});
