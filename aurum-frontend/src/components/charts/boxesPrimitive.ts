/**
 * Lightweight Charts (TradingView) series primitive that paints rectangles and horizontal segments between bar
 * times: position boxes, zones, structure levels. Coordinates are recomputed on every draw, so boxes follow
 * panning, zooming and resizes. Boxes with `to: null` extend to the right edge (into the right offset).
 */
import type {
  IChartApiBase,
  ISeriesApi,
  ISeriesPrimitive,
  ISeriesPrimitivePaneRenderer,
  ISeriesPrimitivePaneView,
  SeriesAttachedParameter,
  SeriesType,
  Time,
  UTCTimestamp,
} from 'lightweight-charts';
import type { Box } from '@/components/signals/signalDrawing';

export class BoxesPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApiBase<Time> | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private boxes: Box[] = [];
  private readonly view: ISeriesPrimitivePaneView;

  constructor() {
    const renderer: ISeriesPrimitivePaneRenderer = {
      draw: (target) => {
        const chart = this.chart;
        const series = this.series;
        if (!chart || !series || this.boxes.length === 0) return;
        const ts = chart.timeScale();
        target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
          const width = bitmapSize.width;
          for (const b of this.boxes) {
            const x1m = ts.timeToCoordinate(b.from as UTCTimestamp);
            if (x1m == null) continue;
            const x2m = b.to == null ? null : ts.timeToCoordinate(b.to as UTCTimestamp);
            const yTop = series.priceToCoordinate(b.top);
            const yBottom = series.priceToCoordinate(b.bottom);
            if (yTop == null || yBottom == null) continue;
            const x1 = Math.round(x1m * hr);
            const x2 = x2m == null ? width : Math.round(x2m * hr);
            if (x2 <= x1) continue;
            const y1 = Math.round(Math.min(yTop, yBottom) * vr);
            const y2 = Math.round(Math.max(yTop, yBottom) * vr);
            const segment = b.top === b.bottom;
            ctx.save();
            if (b.dashed) ctx.setLineDash([4 * hr, 3 * hr]);
            ctx.lineWidth = Math.max(1, Math.round(hr));
            ctx.strokeStyle = b.stroke;
            if (segment) {
              ctx.beginPath();
              ctx.moveTo(x1, y1 + 0.5);
              ctx.lineTo(x2, y1 + 0.5);
              ctx.stroke();
            } else {
              ctx.fillStyle = b.fill;
              ctx.fillRect(x1, y1, x2 - x1, Math.max(1, y2 - y1));
              ctx.strokeRect(x1 + 0.5, y1 + 0.5, x2 - x1 - 1, Math.max(1, y2 - y1 - 1));
            }
            if (b.label) {
              ctx.setLineDash([]);
              ctx.font = `${Math.round(11 * vr)}px Inter, ui-sans-serif, sans-serif`;
              ctx.fillStyle = b.labelColor ?? b.stroke;
              ctx.textBaseline = segment || b.labelAt !== 'bottom' ? 'bottom' : 'top';
              const ty = segment ? y1 - 3 * vr : b.labelAt === 'bottom' ? y2 + 3 * vr : y1 - 3 * vr;
              ctx.fillText(b.label, x1 + 4 * hr, ty);
            }
            ctx.restore();
          }
        });
      },
    };
    this.view = { zOrder: () => 'bottom', renderer: () => renderer };
  }

  attached(p: SeriesAttachedParameter<Time>): void {
    this.chart = p.chart;
    this.series = p.series;
    this.requestUpdate = p.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }

  setBoxes(boxes: Box[]): void {
    this.boxes = boxes;
    this.requestUpdate?.();
  }

  paneViews(): readonly ISeriesPrimitivePaneView[] {
    return [this.view];
  }
}
