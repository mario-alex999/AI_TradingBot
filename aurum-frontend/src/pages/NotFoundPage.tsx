import { Link } from 'react-router-dom';
import { Compass } from 'lucide-react';
import { EmptyState } from '@/components/common/EmptyState';

export default function NotFoundPage() {
  return (
    <EmptyState
      icon={<Compass className="h-5 w-5" aria-hidden />}
      title="Page not found"
      description="The page you requested does not exist."
      action={
        <Link to="/" className="text-sm font-medium text-ink underline decoration-line-control underline-offset-4 hover:decoration-ink">
          Back to dashboard
        </Link>
      }
    />
  );
}
