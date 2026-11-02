import { Card, CardBody } from '@/components/common/Card';
import { PageHeader } from '@/components/common/PageHeader';
import { ChartWorkspace } from '@/components/charts/ChartWorkspace';
import { useChartSelection } from '@/hooks/useChartSelection';
import { OverlayPanel } from '@/components/charts/OverlayPanel';
import { useOverlays } from '@/hooks/queries';

export default function ChartsPage() {
  const { instrument, timeframe, setInstrument, setTimeframe } = useChartSelection();
  const overlays = useOverlays(instrument, timeframe);
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Technical analysis"
        title="Charts & Analysis"
        description="Interactive candlesticks with configurable indicators. Scroll to pan, wheel or pinch to zoom, double-click the price scale to auto-fit."
      />
      <div className="grid gap-5 2xl:grid-cols-[minmax(0,1fr)_300px]">
        <ChartWorkspace instrument={instrument} timeframe={timeframe} onInstrument={setInstrument} onTimeframe={setTimeframe} height="h-[540px]" />
        <Card>
          <CardBody>
            <OverlayPanel available={overlays.data?.data ?? []} />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
