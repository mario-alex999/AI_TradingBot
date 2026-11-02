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
        <Link to="/" className="text-sm font-medium text-gold hover:underline">
          Back to dashboard
        </Link>
      }
    />
  );
}
