import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { QuoteCard } from '@/components/market/QuoteCard';
import type { Quote } from '@/types/market';
import { renderWithProviders } from './render';

const quote: Quote = {
  symbol: 'EURUSD', bid: 1.1008, ask: 1.10088, spread: 8, dailyChangePct: 0.1, dailyHigh: 1.101, dailyLow: 1.099,
  volume: null, status: 'open', digits: 5, updatedAt: '2026-11-17T14:02:30Z',
};

describe('backup prices', () => {
  it('labels a price from the cloud backup feed and explains why', () => {
    renderWithProviders(<QuoteCard quote={{ ...quote, source: 'backup' }} source="live" />);
    const badge = screen.getByText('Backup price');
    expect(badge.closest('[title]')?.getAttribute('title')).toMatch(/MT5 is offline.*Twelve Data.*not your broker/);
  });

  it('shows no label for broker prices', () => {
    renderWithProviders(<QuoteCard quote={{ ...quote, source: 'mt5' }} source="live" />);
    expect(screen.queryByText('Backup price')).not.toBeInTheDocument();
  });
});
