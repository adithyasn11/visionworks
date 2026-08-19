// Loading state for the organisations list.
import React from 'react';
import { SkeletonPage, SkeletonHeader, SkeletonFilters, SkeletonTable } from '../components/Skeleton';

export default function Loading() {
  return (
    <SkeletonPage>
      <SkeletonHeader />
      <SkeletonFilters />
      <SkeletonTable rows={6} cols={7} />
    </SkeletonPage>
  );
}
