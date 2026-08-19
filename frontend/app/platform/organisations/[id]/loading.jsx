// Loading state for one organisation.
// Five stacked sections, matching members / sites / cameras / zones / runs.
import React from 'react';
import { SkeletonPage, SkeletonHeader, SkeletonTiles, SkeletonCard } from '../../components/Skeleton';

export default function Loading() {
  return (
    <SkeletonPage>
      <SkeletonHeader />
      <SkeletonTiles count={4} cols="grid-cols-2 sm:grid-cols-4" />
      <SkeletonCard rows={2} headerWidth="w-28" />
      <SkeletonCard rows={2} headerWidth="w-20" />
      <SkeletonCard rows={4} headerWidth="w-28" />
      <SkeletonCard rows={4} headerWidth="w-24" />
      <SkeletonCard rows={3} headerWidth="w-32" />
    </SkeletonPage>
  );
}
