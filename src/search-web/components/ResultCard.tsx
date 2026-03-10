interface ResultCardProps {
  rank: number;
  id: string;
  query: string;
  text: string;
  metadata: {
    title?: string;
    heading?: string;
    url?: string;
    source?: string;
    file_name?: string;
  };
  rrfScore?: number | null;
  rerankScore?: number | null;
}

const API_URL = "http://localhost:8000";

export default function ResultCard({
  rank,
  id,
  query,
  text,
  metadata,
  rrfScore,
  rerankScore,
}: ResultCardProps) {
  const handleClick = () => {
    fetch(`${API_URL}/click`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, doc_id: id, rank }),
    }).catch(() => {});
  };

  return (
    <div
      onClick={handleClick}
      className="bg-white border border-gray-200 rounded-lg p-5 hover:shadow-md transition-shadow cursor-pointer"
    >
      <div className="flex items-start justify-between gap-4 mb-3">
        <div className="flex items-center gap-3">
          <span className="flex-shrink-0 w-8 h-8 bg-blue-100 text-blue-700 rounded-full flex items-center justify-center text-sm font-bold">
            {rank}
          </span>
          <div>
            <h3 className="font-semibold text-gray-900 leading-tight">
              {metadata.title || "제목 없음"}
            </h3>
            {metadata.heading && (
              <p className="text-sm text-gray-500 mt-0.5">{metadata.heading}</p>
            )}
          </div>
        </div>

        <div className="flex gap-2 text-xs text-gray-400 flex-shrink-0">
          {rrfScore != null && <span>RRF {rrfScore.toFixed(4)}</span>}
          {rerankScore != null && <span>Rerank {rerankScore.toFixed(3)}</span>}
        </div>
      </div>

      <p className="text-gray-700 text-sm leading-relaxed whitespace-pre-wrap line-clamp-4">
        {text}
      </p>

      <div className="flex items-center gap-3 mt-3 text-xs text-gray-400">
        {metadata.source && (
          <span className="bg-gray-100 px-2 py-0.5 rounded">{metadata.source}</span>
        )}
        {metadata.file_name && <span>{metadata.file_name}</span>}
        {metadata.url && (
          <a
            href={metadata.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-500 hover:underline ml-auto"
            onClick={(e) => e.stopPropagation()}
          >
            Notion에서 보기
          </a>
        )}
      </div>
    </div>
  );
}
