// Loading state for the operators page.
import React from 'react';
import { SkeletonPage, SkeletonHeader, SkeletonTiles, SkeletonCard } from '../components/Skeleton';

export default function Loading() {
  return (
    <SkeletonPage>
      <SkeletonHeader />
      <SkeletonTiles count={3} cols="grid-cols-2 sm:grid-cols-3" />
      <SkeletonCard rows={2} headerWidth="w-72" />
      <SkeletonCard rows={2} headerWidth="w-28" />
      <SkeletonCard rows={2} headerWidth="w-48" />
    </SkeletonPage>
  );
}
