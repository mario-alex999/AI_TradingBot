import { PageHeader } from '@/components/common/PageHeader';
import { ConnectionStatusPanel } from '@/components/data-centre/ConnectionStatusPanel';
import { CollectionControls } from '@/components/data-centre/CollectionControls';
import { InstrumentConfigTable } from '@/components/data-centre/InstrumentConfigTable';
import { TimeframeMonitor } from '@/components/data-centre/TimeframeMonitor';
import { DataQualityPanel } from '@/components/data-centre/DataQualityPanel';
import { CollectionLogsTable } from '@/components/data-centre/CollectionLogsTable';

export default function DataCentrePage() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Module 01"
        title="Data Centre"
        description="Monitor the MetaTrader 5 market-data pipeline: connections, instrument configuration, collection health and data quality."
      />
      <ConnectionStatusPanel />
      <CollectionControls />
      <InstrumentConfigTable />
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <TimeframeMonitor />
        <DataQualityPanel />
      </div>
      <CollectionLogsTable />
    </div>
  );
}
