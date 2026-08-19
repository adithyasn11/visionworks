// Loading state for /platform (Overview).
// Mirrors the real page: header + action, six tiles, chart beside the attention
// list, then recent signups beside the "right now" column.
import React from 'react';
import { SkeletonPage, SkeletonHeader, SkeletonTiles, SkeletonCard } from './components/Skeleton';

export default function Loading() {
  return (
    <SkeletonPage>
      <SkeletonHeader withAction />
      <SkeletonTiles count={6} />
      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4">
        <SkeletonCard tall headerWidth="w-52" />
        <SkeletonCard rows={3} headerWidth="w-36" />
      </div>
      <div className="grid lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-4">
        <SkeletonCard rows={5} headerWidth="w-32" />
        <SkeletonCard rows={3} headerWidth="w-24" />
      </div>
    </SkeletonPage>
  );
}
