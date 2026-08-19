// Loading state for the audit log.
import React from 'react';
import { SkeletonPage, SkeletonHeader, SkeletonFilters, SkeletonCard } from '../components/Skeleton';

export default function Loading() {
  return (
    <SkeletonPage>
      <SkeletonHeader />
      <SkeletonFilters />
      <SkeletonCard rows={8} headerWidth="w-32" />
    </SkeletonPage>
  );
}
