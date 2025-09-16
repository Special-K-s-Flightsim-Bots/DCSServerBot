FROM ruby:3.1

ENV LC_ALL=C.UTF-8
ENV LANG=en_US.UTF-8
ENV LANGUAGE=en_US.UTF-8
ENV JEKYLL_LOG_LEVEL=debug

WORKDIR /usr/src/app

COPY . .
RUN mv README.md index.md
RUN gem install bundler && bundle install

RUN bundle exec jekyll build --baseurl "/DCSServerBot" --verbose --trace

EXPOSE 4000
